#include "GraphBody.h"

#include "Engine/Blueprint.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/MemberReference.h"
#include "UObject/UnrealType.h"

namespace
{
	// The pin category of an execution pin. UEdGraphSchema_K2 names this constant, and that class
	// lives in BlueprintGraph, which an early-loading module must not link.
	const FName ExecCategory(TEXT("exec"));

	const int32 MaxExpressionDepth = 6;
	const int32 MaxStatements = 2000;
	const int32 MaxMacroDepth = 8;

	/** One macro instance being rendered inline, and the caller graph it substitutes back into. */
	struct FMacroFrame
	{
		const UEdGraphNode* Instance = nullptr;
		const UEdGraphNode* EntryTunnel = nullptr;
		const UEdGraphNode* ExitTunnel = nullptr;
	};

	typedef TArray<FMacroFrame, TInlineAllocator<8>> FFrameStack;

	/** A node is visited once PER CALL SITE. The nodes of a macro graph are shared by every
	 *  instance of that macro, so a key of the node alone renders the second occurrence as a
	 *  `goto` and the body is seen once however many times the macro is called. */
	typedef TPair<const UEdGraphNode*, uint64> FVisitKey;

	uint64 FrameKey(const FFrameStack& Frames)
	{
		uint64 Key = 0;
		for (const FMacroFrame& Frame : Frames)
		{
			Key = Key * 1099511628211ULL
				^ static_cast<uint64>(reinterpret_cast<UPTRINT>(Frame.Instance));
		}
		return Key;
	}

	FString Indent(int32 Depth)
	{
		return FString::ChrN(Depth * 2, TEXT(' '));
	}

	/** The node's own title, on one line. */
	FString TitleOf(const UEdGraphNode* Node)
	{
		if (Node == nullptr)
		{
			return TEXT("<none>");
		}

		FString Title = Node->GetNodeTitle(ENodeTitleType::ListView).ToString();
		Title.ReplaceInline(TEXT("\r"), TEXT(""));
		Title.ReplaceInline(TEXT("\n"), TEXT(" "));
		Title.TrimStartAndEndInline();
		return Title.IsEmpty() ? Node->GetClass()->GetName() : Title;
	}

	bool IsExec(const UEdGraphPin* Pin)
	{
		return Pin != nullptr && Pin->PinType.PinCategory == ExecCategory;
	}

	/** A reroute node is wiring, not behaviour. Follow it to the node that produced the value. */
	bool IsReroute(const UEdGraphNode* Node)
	{
		return Node != nullptr && Node->GetClass()->GetName() == TEXT("K2Node_Knot");
	}

	bool IsMacroInstance(const UEdGraphNode* Node)
	{
		return Node != nullptr && Node->GetClass()->GetName() == TEXT("K2Node_MacroInstance");
	}

	bool IsTunnel(const UEdGraphNode* Node)
	{
		if (Node == nullptr)
		{
			return false;
		}
		const FString Name = Node->GetClass()->GetName();
		return Name == TEXT("K2Node_Tunnel") || Name == TEXT("K2Node_TunnelBoundary");
	}

	/** A statement node has an execution pin. Everything else is an expression. */
	bool IsStatement(const UEdGraphNode* Node)
	{
		if (Node == nullptr)
		{
			return false;
		}

		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (IsExec(Pin))
			{
				return true;
			}
		}
		return false;
	}

	/** A split struct pin is hidden and its sub-pins carry the values, so a sub-pin is usable. */
	bool IsUsablePin(const UEdGraphPin* Pin)
	{
		return Pin != nullptr && !Pin->bHidden && !Pin->bOrphanedPin;
	}

	// ---- macro expansion ----------------------------------------------------------------------

	/** The graph a `K2Node_MacroInstance` points at.
	 *
	 *  `FGraphReference` is `ENGINE_API` and its `MacroGraphReference` property is reflected, so
	 *  the graph is reachable without linking BlueprintGraph, which an early-loading module must
	 *  not do. */
	const FGraphReference* MacroReferenceOf(const UEdGraphNode* Node)
	{
		if (!IsMacroInstance(Node))
		{
			return nullptr;
		}

		FStructProperty* Property = CastField<FStructProperty>(
			Node->GetClass()->FindPropertyByName(FName(TEXT("MacroGraphReference"))));
		if (Property == nullptr || Property->Struct != FGraphReference::StaticStruct())
		{
			return nullptr;
		}

		return Property->ContainerPtrToValuePtr<FGraphReference>(Node);
	}

	/** Read a reflected bool bitfield, for the tunnel flags that say which end a tunnel is. */
	bool BoolPropertyOf(const UEdGraphNode* Node, const TCHAR* PropertyName)
	{
		FBoolProperty* Property =
			CastField<FBoolProperty>(Node->GetClass()->FindPropertyByName(FName(PropertyName)));
		return Property != nullptr && Property->GetPropertyValue_InContainer(Node);
	}

	/** A macro's two ends. The entry tunnel feeds the body; the exit tunnel returns from it. */
	void TunnelsOf(const UEdGraph* Macro, const UEdGraphNode*& OutEntry, const UEdGraphNode*& OutExit)
	{
		OutEntry = nullptr;
		OutExit = nullptr;
		if (Macro == nullptr)
		{
			return;
		}

		for (const UEdGraphNode* Node : Macro->Nodes)
		{
			if (!IsTunnel(Node))
			{
				continue;
			}

			// The entry tunnel hands values OUT into the body; the exit tunnel takes them IN.
			const bool bHasOutputs = BoolPropertyOf(Node, TEXT("bCanHaveOutputs"));
			const bool bHasInputs = BoolPropertyOf(Node, TEXT("bCanHaveInputs"));
			if (bHasOutputs && !bHasInputs && OutEntry == nullptr)
			{
				OutEntry = Node;
			}
			else if (bHasInputs && !bHasOutputs && OutExit == nullptr)
			{
				OutExit = Node;
			}
		}
	}

	/** Is this macro authored in the project?
	 *
	 *  The engine's own StandardMacros hold loop and validity plumbing. Rendering their internals
	 *  buries the caller's logic in index arithmetic, and the caller already shows every exec
	 *  output of the instance node. */
	bool IsProjectMacro(const FGraphReference* Reference)
	{
		if (Reference == nullptr)
		{
			return false;
		}
		const UBlueprint* Owner = Reference->GetBlueprint();
		if (Owner == nullptr)
		{
			return false;
		}
		return Owner->GetOutermost()->GetName().StartsWith(TEXT("/Game/"));
	}

	/** The pin of this node with that name and direction. */
	const UEdGraphPin* PinByName(const UEdGraphNode* Node, const FName& Name,
	                             EEdGraphPinDirection Want)
	{
		if (Node == nullptr)
		{
			return nullptr;
		}
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin->Direction == Want && Pin->PinName == Name)
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/** The instance-node pin that stands for this tunnel pin. Matching is by name and direction. */
	const UEdGraphPin* PinAcross(const UEdGraphNode* Instance, const UEdGraphPin* TunnelPin,
	                             EEdGraphPinDirection Want)
	{
		return TunnelPin != nullptr ? PinByName(Instance, TunnelPin->PinName, Want) : nullptr;
	}

	/** The two ends of the macro this node instances, when the macro is authored in the project. */
	bool ProjectMacroOf(const UEdGraphNode* Node, const UEdGraphNode*& OutEntry,
	                    const UEdGraphNode*& OutExit)
	{
		OutEntry = nullptr;
		OutExit = nullptr;
		const FGraphReference* Reference = MacroReferenceOf(Node);
		if (!IsProjectMacro(Reference))
		{
			return false;
		}
		TunnelsOf(Reference->GetGraph(), OutEntry, OutExit);
		return OutEntry != nullptr;
	}

	// ---- rendering ----------------------------------------------------------------------------

	/** The literal a pin carries when nothing is wired to it. */
	FString LiteralOf(const UEdGraphPin* Pin)
	{
		if (Pin->DefaultObject != nullptr)
		{
			return Pin->DefaultObject->GetName();
		}
		if (!Pin->DefaultTextValue.IsEmpty())
		{
			return FString::Printf(TEXT("\"%s\""), *Pin->DefaultTextValue.ToString());
		}
		if (!Pin->DefaultValue.IsEmpty())
		{
			return Pin->DefaultValue;
		}
		return TEXT("None");
	}

	/** A source pin, and the macro frames it is rendered under. A source reached through a macro's
	 *  entry tunnel lives in the caller, one frame out. */
	struct FSource
	{
		const UEdGraphPin* Pin = nullptr;
		FFrameStack Frames;
	};

	/** Step through reroutes, and out of a macro through its entry tunnel, to every pin that really
	 *  carries a value into Pin. A target pin can take several links, and each one is a source.
	 *  `Frames` is the stack of macro instances being rendered inline; a pin on the innermost entry
	 *  tunnel resolves to the caller's argument one frame out. */
	void Upstream(const UEdGraphPin* Pin, FFrameStack& Frames, TArray<FSource>& OutSources,
	              int32 Hops = 0)
	{
		if (Pin == nullptr || Hops >= 64)
		{
			return;
		}

		for (const UEdGraphPin* Source : Pin->LinkedTo)
		{
			if (Source == nullptr)
			{
				continue;
			}

			const UEdGraphNode* Owner = Source->GetOwningNodeUnchecked();

			if (IsReroute(Owner))
			{
				// A reroute has one input and one output. Take its input and keep going.
				for (const UEdGraphPin* KnotPin : Owner->Pins)
				{
					if (KnotPin->Direction == EGPD_Input)
					{
						Upstream(KnotPin, Frames, OutSources, Hops + 1);
						break;
					}
				}
				continue;
			}

			if (Frames.Num() > 0 && Owner == Frames.Last().EntryTunnel)
			{
				// The value comes from the macro's own input, so it is the caller's argument.
				const FMacroFrame Frame = Frames.Pop();
				Upstream(PinAcross(Frame.Instance, Source, EGPD_Input), Frames, OutSources, Hops + 1);
				Frames.Push(Frame);
				continue;
			}

			FSource Found;
			Found.Pin = Source;
			Found.Frames = Frames;
			OutSources.Add(Found);
		}
	}

	FString RenderExpression(const UEdGraphPin* Pin, int32 Depth, FFrameStack& Frames);

	/** `Title(a: x, b: y)` for a pure node, or `<Title>` for the output of a statement. */
	FString RenderNodeExpression(const UEdGraphNode* Node, const UEdGraphPin* OutPin, int32 Depth,
	                             FFrameStack& Frames)
	{
		const FString Title = TitleOf(Node);

		// A pure macro authored in the project is a rule written as a graph. Resolve the value
		// through its exit tunnel so the caller shows what the rule computes.
		if (!IsStatement(Node) && Depth < MaxExpressionDepth && Frames.Num() < MaxMacroDepth)
		{
			const UEdGraphNode* Entry = nullptr;
			const UEdGraphNode* Exit = nullptr;
			if (ProjectMacroOf(Node, Entry, Exit) && Exit != nullptr && OutPin != nullptr)
			{
				const UEdGraphPin* Inner = PinByName(Exit, OutPin->PinName, EGPD_Input);
				if (Inner != nullptr)
				{
					FMacroFrame Frame;
					Frame.Instance = Node;
					Frame.EntryTunnel = Entry;
					Frame.ExitTunnel = Exit;
					Frames.Push(Frame);
					const FString Value = RenderExpression(Inner, Depth + 1, Frames);
					Frames.Pop();
					return FString::Printf(TEXT("%s{%s}"), *Title, *Value);
				}
			}
		}

		// The output of a statement is a value that statement already produced. Name the statement
		// and the pin, so a parameter of an event reads as the parameter rather than as the event.
		if (IsStatement(Node) || Depth >= MaxExpressionDepth)
		{
			if (OutPin != nullptr && !OutPin->PinName.IsNone())
			{
				return FString::Printf(TEXT("<%s>.%s"), *Title, *OutPin->PinName.ToString());
			}
			return FString::Printf(TEXT("<%s>"), *Title);
		}

		TArray<FString> Args;
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin->Direction != EGPD_Input || IsExec(Pin) || !IsUsablePin(Pin))
			{
				continue;
			}
			Args.Add(FString::Printf(TEXT("%s: %s"), *Pin->PinName.ToString(),
			                         *RenderExpression(Pin, Depth + 1, Frames)));
		}

		// A pure node whose output has a name of its own reads better with it, as `Get myVar`.
		if (Args.Num() == 0)
		{
			return Title;
		}

		return FString::Printf(TEXT("%s(%s)"), *Title, *FString::Join(Args, TEXT(", ")));
	}

	/** The value of an input pin: its literal, its one source, or `[a, b]` when several links feed
	 *  it, as a call on more than one target. */
	FString RenderExpression(const UEdGraphPin* Pin, int32 Depth, FFrameStack& Frames)
	{
		TArray<FSource> Sources;
		Upstream(Pin, Frames, Sources);
		if (Sources.Num() == 0)
		{
			return LiteralOf(Pin);
		}

		TArray<FString> Values;
		for (FSource& Source : Sources)
		{
			Values.Add(RenderNodeExpression(Source.Pin->GetOwningNodeUnchecked(), Source.Pin, Depth,
			                                Source.Frames));
		}

		if (Values.Num() == 1)
		{
			return Values[0];
		}
		return FString::Printf(TEXT("[%s]"), *FString::Join(Values, TEXT(", ")));
	}

	void RenderStatement(const UEdGraphNode* Node, int32 Depth, TArray<FString>& OutLines,
	                     TSet<FVisitKey>& Visited, FFrameStack& Frames);

	/** Continue the chain after an exec output, following it out of a macro when it ends on the
	 *  exit tunnel. */
	void RenderNext(const UEdGraphPin* ExecOut, int32 Depth, TArray<FString>& OutLines,
	                TSet<FVisitKey>& Visited, FFrameStack& Frames)
	{
		const UEdGraphPin* Next = ExecOut != nullptr && ExecOut->LinkedTo.Num() > 0
			? ExecOut->LinkedTo[0] : nullptr;
		if (Next == nullptr)
		{
			return;
		}

		const UEdGraphNode* Owner = Next->GetOwningNodeUnchecked();
		if (IsReroute(Owner))
		{
			for (const UEdGraphPin* KnotPin : Owner->Pins)
			{
				if (KnotPin->Direction == EGPD_Output && IsExec(KnotPin))
				{
					RenderNext(KnotPin, Depth, OutLines, Visited, Frames);
					return;
				}
			}
			return;
		}

		if (Frames.Num() > 0 && Owner == Frames.Last().ExitTunnel)
		{
			// The macro is finished. Leave through the instance node's matching output.
			const FMacroFrame Frame = Frames.Pop();
			const UEdGraphPin* Outer = PinAcross(Frame.Instance, Next, EGPD_Output);
			RenderNext(Outer, Depth, OutLines, Visited, Frames);
			Frames.Push(Frame);
			return;
		}

		RenderStatement(Owner, Depth, OutLines, Visited, Frames);
	}

	/** One statement and everything it leads to. */
	void RenderStatement(const UEdGraphNode* Node, int32 Depth, TArray<FString>& OutLines,
	                     TSet<FVisitKey>& Visited, FFrameStack& Frames)
	{
		if (Node == nullptr || OutLines.Num() >= MaxStatements)
		{
			return;
		}

		const FVisitKey Key(Node, FrameKey(Frames));
		if (Visited.Contains(Key))
		{
			OutLines.Add(Indent(Depth) + FString::Printf(TEXT("goto %s"), *TitleOf(Node)));
			return;
		}
		Visited.Add(Key);

		OutLines.Add(Indent(Depth) + TitleOf(Node));

		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin->Direction != EGPD_Input || IsExec(Pin) || !IsUsablePin(Pin))
			{
				continue;
			}
			OutLines.Add(Indent(Depth + 1)
				+ FString::Printf(TEXT("%s = %s"), *Pin->PinName.ToString(),
				                  *RenderExpression(Pin, 0, Frames)));
		}

		// A macro authored in the project holds game rules. Render its body here, bound to this
		// instance's arguments, so each occurrence carries what it does.
		if (Frames.Num() < MaxMacroDepth)
		{
			const UEdGraphNode* Entry = nullptr;
			const UEdGraphNode* Exit = nullptr;
			if (ProjectMacroOf(Node, Entry, Exit))
			{
				FMacroFrame Frame;
				Frame.Instance = Node;
				Frame.EntryTunnel = Entry;
				Frame.ExitTunnel = Exit;
				Frames.Push(Frame);

				const int32 Before = OutLines.Num();
				for (const UEdGraphPin* Pin : Entry->Pins)
				{
					if (Pin->Direction == EGPD_Output && IsExec(Pin) && IsUsablePin(Pin))
					{
						RenderNext(Pin, Depth + 1, OutLines, Visited, Frames);
					}
				}
				if (OutLines.Num() == Before)
				{
					OutLines.Add(Indent(Depth + 1) + TEXT("(macro body: no statement)"));
				}

				Frames.Pop();
				return;
			}
		}

		// Count the execution outputs first: one continues the chain, several are branches.
		TArray<const UEdGraphPin*> ExecOuts;
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin->Direction == EGPD_Output && IsExec(Pin) && IsUsablePin(Pin))
			{
				ExecOuts.Add(Pin);
			}
		}

		for (const UEdGraphPin* Pin : ExecOuts)
		{
			if (ExecOuts.Num() == 1)
			{
				RenderNext(Pin, Depth, OutLines, Visited, Frames);
				continue;
			}

			OutLines.Add(Indent(Depth + 1) + Pin->PinName.ToString() + TEXT(":"));
			const int32 Before = OutLines.Num();
			RenderNext(Pin, Depth + 2, OutLines, Visited, Frames);
			if (OutLines.Num() == Before)
			{
				OutLines.Add(Indent(Depth + 2) + TEXT("(nothing)"));
			}
		}
	}

	/** The FName in a member reference held under StructPropertyName on this node. */
	FName MemberNameOf(const UEdGraphNode* Node, const TCHAR* StructPropertyName)
	{
		FStructProperty* Property =
			CastField<FStructProperty>(Node->GetClass()->FindPropertyByName(FName(StructPropertyName)));
		if (Property == nullptr || Property->Struct != FMemberReference::StaticStruct())
		{
			return NAME_None;
		}

		const FMemberReference* Reference = Property->ContainerPtrToValuePtr<FMemberReference>(Node);
		return Reference != nullptr ? Reference->GetMemberName() : NAME_None;
	}

	FName NamePropertyOf(const UEdGraphNode* Node, const TCHAR* PropertyName)
	{
		FNameProperty* Property =
			CastField<FNameProperty>(Node->GetClass()->FindPropertyByName(FName(PropertyName)));
		return Property != nullptr ? Property->GetPropertyValue_InContainer(Node) : NAME_None;
	}

	/** Is the node a `K2Node_Event` or one of its subclasses? The class is in BlueprintGraph, so it
	 *  is matched by name along the class chain. */
	bool IsEventNode(const UEdGraphNode* Node)
	{
		for (const UClass* Class = Node->GetClass(); Class != nullptr; Class = Class->GetSuperClass())
		{
			if (Class->GetName() == TEXT("K2Node_Event"))
			{
				return true;
			}
		}
		return false;
	}
}

void FGraphBody::Render(const UEdGraphNode* Start, TArray<FString>& OutLines)
{
	OutLines.Reset();
	if (Start == nullptr)
	{
		return;
	}

	TSet<FVisitKey> Visited;
	Visited.Add(FVisitKey(Start, 0));
	FFrameStack Frames;

	// The entry node itself is the signature, which the JSON already carries. Start at what it runs.
	for (const UEdGraphPin* Pin : Start->Pins)
	{
		if (Pin->Direction == EGPD_Output && IsExec(Pin) && IsUsablePin(Pin))
		{
			RenderNext(Pin, 0, OutLines, Visited, Frames);
		}
	}

	// A pure macro has no execution to walk. Its body is the expression on each output of its
	// exit tunnel.
	if (OutLines.Num() == 0 && IsTunnel(Start))
	{
		const UEdGraphNode* Entry = nullptr;
		const UEdGraphNode* Exit = nullptr;
		TunnelsOf(Start->GetGraph(), Entry, Exit);
		if (Exit != nullptr)
		{
			for (const UEdGraphPin* Pin : Exit->Pins)
			{
				if (Pin->Direction != EGPD_Input || IsExec(Pin) || !IsUsablePin(Pin))
				{
					continue;
				}
				OutLines.Add(FString::Printf(TEXT("%s = %s"), *Pin->PinName.ToString(),
				                             *RenderExpression(Pin, 0, Frames)));
			}
		}
	}

	if (OutLines.Num() >= MaxStatements)
	{
		OutLines.Add(FString::Printf(TEXT("... stopped at %d statements"), MaxStatements));
	}
}

UEdGraphNode* FGraphBody::FindEntryNode(const UEdGraph* Graph)
{
	if (Graph == nullptr)
	{
		return nullptr;
	}

	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && Node->GetClass()->GetName() == TEXT("K2Node_FunctionEntry"))
		{
			return Node;
		}
	}

	// A macro has a tunnel pair rather than a function entry. Its entry end is the one that hands
	// values out into the body.
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (IsTunnel(Node) && BoolPropertyOf(Node, TEXT("bCanHaveOutputs"))
			&& !BoolPropertyOf(Node, TEXT("bCanHaveInputs")))
		{
			return Node;
		}
	}
	return nullptr;
}

UEdGraphNode* FGraphBody::FindEventNode(const TArray<UEdGraph*>& Graphs, const FName& EventName)
{
	for (const UEdGraph* Graph : Graphs)
	{
		if (Graph == nullptr)
		{
			continue;
		}

		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}

			// A custom, component-bound, actor-bound or input event names its function in
			// CustomFunctionName (`BndEvt__...`); an override names it in its EventReference.
			if (IsEventNode(Node)
				&& (NamePropertyOf(Node, TEXT("CustomFunctionName")) == EventName
					|| MemberNameOf(Node, TEXT("EventReference")) == EventName))
			{
				return Node;
			}
		}
	}
	return nullptr;
}

UEdGraph* FGraphBody::GraphOf(const UEdGraphNode* Node)
{
	return Node != nullptr ? Node->GetGraph() : nullptr;
}
