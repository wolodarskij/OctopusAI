#include "BlueprintSummary.h"

#include "GraphBody.h"
#include "Misc/EngineVersionComparison.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "AssetTextExporter.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UnrealType.h"
#include "UObject/UObjectGlobals.h"

typedef TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>> FSummaryWriter;

namespace
{
	// int32, FString, AMyActor*, TArray<FVector> - the type as C++ spells it.
	FString TypeOf(const FProperty* Property)
	{
		if (Property == nullptr)
		{
			return FString();
		}

		FString Extended;
		FString Base = Property->GetCPPType(&Extended, 0);
		return Base + Extended;
	}

	// in, out or return. An out parameter that is also const is an input.
	FString DirectionOf(const FProperty* Property)
	{
		if (Property->HasAnyPropertyFlags(CPF_ReturnParm))
		{
			return TEXT("return");
		}
		if (Property->HasAnyPropertyFlags(CPF_OutParm) && !Property->HasAnyPropertyFlags(CPF_ConstParm))
		{
			return TEXT("out");
		}
		return TEXT("in");
	}

	void WriteParams(const TSharedRef<FSummaryWriter>& Writer, const UFunction* Function)
	{
		Writer->WriteArrayStart(TEXT("params"));
		if (Function != nullptr)
		{
			for (TFieldIterator<FProperty> It(Function); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
			{
				Writer->WriteObjectStart();
				Writer->WriteValue(TEXT("name"), It->GetName());
				Writer->WriteValue(TEXT("type"), TypeOf(*It));
				Writer->WriteValue(TEXT("dir"), DirectionOf(*It));
				Writer->WriteObjectEnd();
			}
		}
		Writer->WriteArrayEnd();
	}

	FString NetModeOf(const UFunction* Function)
	{
		if (Function->HasAnyFunctionFlags(FUNC_NetMulticast)) return TEXT("Multicast");
		if (Function->HasAnyFunctionFlags(FUNC_NetServer))    return TEXT("Server");
		if (Function->HasAnyFunctionFlags(FUNC_NetClient))    return TEXT("Client");
		return TEXT("NotReplicated");
	}

	FString AccessOf(const UFunction* Function)
	{
		if (Function->HasAnyFunctionFlags(FUNC_Private))   return TEXT("private");
		if (Function->HasAnyFunctionFlags(FUNC_Protected)) return TEXT("protected");
		return TEXT("public");
	}

	// The specifiers a reader acts on, taken from the variable's PropertyFlags.
	void WriteVariableFlags(const TSharedRef<FSummaryWriter>& Writer, uint64 Flags)
	{
		// CPF_Edit with the two disable flags is one specifier. Both disabled edits nowhere.
		const bool bEdit = (Flags & CPF_Edit) != 0;
		const bool bNoInstance = (Flags & CPF_DisableEditOnInstance) != 0;
		const bool bNoTemplate = (Flags & CPF_DisableEditOnTemplate) != 0;

		Writer->WriteArrayStart(TEXT("flags"));
		if (bEdit && !bNoInstance && !bNoTemplate) Writer->WriteValue(TEXT("EditAnywhere"));
		if (bEdit && bNoInstance && !bNoTemplate)  Writer->WriteValue(TEXT("EditDefaultsOnly"));
		if (bEdit && !bNoInstance && bNoTemplate)  Writer->WriteValue(TEXT("EditInstanceOnly"));
		if (Flags & CPF_BlueprintVisible)      Writer->WriteValue(TEXT("BlueprintVisible"));
		if (Flags & CPF_BlueprintReadOnly)     Writer->WriteValue(TEXT("BlueprintReadOnly"));
		if (Flags & CPF_Net)                   Writer->WriteValue(TEXT("Replicated"));
		if (Flags & CPF_RepNotify)             Writer->WriteValue(TEXT("RepNotify"));
		if (Flags & CPF_SaveGame)              Writer->WriteValue(TEXT("SaveGame"));
		if (Flags & CPF_Transient)             Writer->WriteValue(TEXT("Transient"));
		if (Flags & CPF_Config)                Writer->WriteValue(TEXT("Config"));
		if (Flags & CPF_Interp)                Writer->WriteValue(TEXT("ExposeToCinematics"));
		Writer->WriteArrayEnd();
	}

	FString ContainerOf(const FEdGraphPinType& PinType)
	{
		switch (PinType.ContainerType)
		{
		case EPinContainerType::Array: return TEXT("Array");
		case EPinContainerType::Set:   return TEXT("Set");
		case EPinContainerType::Map:   return TEXT("Map");
		default:                       return TEXT("Single");
		}
	}

	// The pin's category plus the class or struct it names, such as object:AMyActor.
	FString PinTypeOf(const FEdGraphPinType& PinType)
	{
		FString Text = PinType.PinCategory.ToString();
		if (!PinType.PinSubCategory.IsNone())
		{
			Text += TEXT(":") + PinType.PinSubCategory.ToString();
		}
		if (const UObject* SubObject = PinType.PinSubCategoryObject.Get())
		{
			Text += TEXT(":") + SubObject->GetName();
		}
		return Text;
	}

	FString MetaDataOf(const FBPVariableDescription& Variable, const FName& Key)
	{
		for (const FBPVariableMetaDataEntry& Entry : Variable.MetaDataArray)
		{
			if (Entry.DataKey == Key)
			{
				return Entry.DataValue;
			}
		}
		return FString();
	}

	// The authored default of a variable lives on the class default object, not in the variable's
	// own description, which is empty for most of them.
	FString DefaultOf(UClass* Generated, const FName& VarName)
	{
		if (Generated == nullptr)
		{
			return FString();
		}

		UObject* CDO = Generated->GetDefaultObject(false);
		FProperty* Property = Generated->FindPropertyByName(VarName);
		if (CDO == nullptr || Property == nullptr || Property->ArrayDim != 1)
		{
			return FString();
		}

		FString Value;
#if UE_VERSION_OLDER_THAN(5, 1, 0)
		Property->ExportTextItem(Value, Property->ContainerPtrToValuePtr<void>(CDO), nullptr, CDO, PPF_None);
#else
		Property->ExportTextItem_Direct(Value, Property->ContainerPtrToValuePtr<void>(CDO), nullptr, CDO, PPF_None);
#endif
		return Value;
	}

	void WriteBody(const TSharedRef<FSummaryWriter>& Writer, const UEdGraphNode* Start)
	{
		TArray<FString> Lines;
		FGraphBody::Render(Start, Lines);

		Writer->WriteArrayStart(TEXT("body"));
		for (const FString& Line : Lines)
		{
			Writer->WriteValue(Line);
		}
		Writer->WriteArrayEnd();
	}

	void CountNodes(const TArray<UEdGraph*>& Graphs, TMap<FName, int32>& OutCounts)
	{
		for (const UEdGraph* Graph : Graphs)
		{
			if (Graph != nullptr)
			{
				OutCounts.Add(Graph->GetFName(), Graph->Nodes.Num());
			}
		}
	}
}

bool FBlueprintSummary::BuildSummary(const FString& AssetPath, bool bBodies,
                                            FString& OutJson, FString& OutError)
{
	OutJson.Empty();
	OutError.Empty();

	const FString ObjectPath = FAssetTextExporter::ToObjectPath(AssetPath);
	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *ObjectPath);
	if (Blueprint == nullptr)
	{
		OutError = FString::Printf(TEXT("'%s' is not a Blueprint"), *ObjectPath);
		return false;
	}

	// The graph arrays decide what a UFunction IS. A name with a graph in FunctionGraphs is a
	// function; every other Blueprint-authored UFunction came from an event node.
	TMap<FName, int32> FunctionNodes;
	CountNodes(Blueprint->FunctionGraphs, FunctionNodes);

	TMap<FName, UEdGraph*> FunctionGraphs;
	for (UEdGraph* Graph : Blueprint->FunctionGraphs)
	{
		if (Graph != nullptr)
		{
			FunctionGraphs.Add(Graph->GetFName(), Graph);
		}
	}

	// An interface function with an output is implemented as a function graph, which the
	// Blueprint keeps with the interface rather than in FunctionGraphs. An interface function
	// without one is an event in the ubergraph. Either way the interface declares the name.
	TMap<FName, FString> InterfaceOf;
	for (const FBPInterfaceDescription& Interface : Blueprint->ImplementedInterfaces)
	{
		if (Interface.Interface == nullptr)
		{
			continue;
		}

		CountNodes(Interface.Graphs, FunctionNodes);
		for (UEdGraph* Graph : Interface.Graphs)
		{
			if (Graph != nullptr)
			{
				FunctionGraphs.Add(Graph->GetFName(), Graph);
			}
		}

		for (TFieldIterator<UFunction> It(Interface.Interface.Get()); It; ++It)
		{
			InterfaceOf.Add(It->GetFName(), Interface.Interface->GetPathName());
		}
	}

	TMap<FName, int32> MacroNodes;
	CountNodes(Blueprint->MacroGraphs, MacroNodes);

	TSet<FName> DispatcherNames;
	for (const UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
	{
		if (Graph != nullptr)
		{
			DispatcherNames.Add(Graph->GetFName());
		}
	}

	TSharedRef<FSummaryWriter> Writer = TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&OutJson);
	Writer->WriteObjectStart();

	Writer->WriteValue(TEXT("asset"), Blueprint->GetOutermost()->GetName());
	Writer->WriteValue(TEXT("name"), Blueprint->GetName());
	Writer->WriteValue(TEXT("class"),
		Blueprint->GeneratedClass != nullptr ? Blueprint->GeneratedClass->GetName() : FString());
	Writer->WriteValue(TEXT("parent"),
		Blueprint->ParentClass != nullptr ? Blueprint->ParentClass->GetPathName() : FString());

	Writer->WriteArrayStart(TEXT("interfaces"));
	for (const FBPInterfaceDescription& Interface : Blueprint->ImplementedInterfaces)
	{
		if (Interface.Interface != nullptr)
		{
			Writer->WriteValue(Interface.Interface->GetPathName());
		}
	}
	Writer->WriteArrayEnd();

	UClass* Generated = Blueprint->GeneratedClass;

	// --- Variables, from the Blueprint's own declarations ------------------------------------------
	Writer->WriteArrayStart(TEXT("variables"));
	for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
	{
		Writer->WriteObjectStart();
		Writer->WriteValue(TEXT("name"), Variable.VarName.ToString());
		Writer->WriteValue(TEXT("type"), PinTypeOf(Variable.VarType));
		Writer->WriteValue(TEXT("container"), ContainerOf(Variable.VarType));
		Writer->WriteValue(TEXT("category"), Variable.Category.ToString());
		Writer->WriteValue(TEXT("default"), DefaultOf(Generated, Variable.VarName));
		Writer->WriteValue(TEXT("repNotify"),
			Variable.RepNotifyFunc.IsNone() ? FString() : Variable.RepNotifyFunc.ToString());
		Writer->WriteValue(TEXT("exposeOnSpawn"), MetaDataOf(Variable, TEXT("ExposeOnSpawn")) == TEXT("true"));
		Writer->WriteValue(TEXT("tooltip"), MetaDataOf(Variable, TEXT("ToolTip")));
		WriteVariableFlags(Writer, Variable.PropertyFlags);
		Writer->WriteObjectEnd();
	}
	Writer->WriteArrayEnd();

	// --- Functions, events and dispatchers, from the generated class -------------------------------
	// The generated class carries the real signature: parameter names, types, direction and the
	// function flags. A graph read cannot see a parameter the compiler resolved.
	TArray<UFunction*> Functions;
	TArray<UFunction*> Events;
	if (Generated != nullptr)
	{
		for (TFieldIterator<UFunction> It(Generated, EFieldIteratorFlags::ExcludeSuper); It; ++It)
		{
			UFunction* Function = *It;
			const FName Name = Function->GetFName();

			// The ubergraph's own body, and every delegate signature, are not declarations a reader
			// of this file is looking for.
			if (Name.ToString().StartsWith(TEXT("ExecuteUbergraph"))) continue;
			if (Function->HasAnyFunctionFlags(FUNC_Delegate)) continue;
			if (DispatcherNames.Contains(Name)) continue;

			if (FunctionNodes.Contains(Name))
			{
				Functions.Add(Function);
			}
			else
			{
				Events.Add(Function);
			}
		}
	}
	else
	{
		OutError = TEXT("no generated class; the Blueprint has not compiled");
	}

	Functions.Sort([](const UFunction& A, const UFunction& B) { return A.GetName() < B.GetName(); });
	Events.Sort([](const UFunction& A, const UFunction& B) { return A.GetName() < B.GetName(); });

	Writer->WriteArrayStart(TEXT("functions"));
	for (const UFunction* Function : Functions)
	{
		Writer->WriteObjectStart();
		Writer->WriteValue(TEXT("name"), Function->GetName());
		Writer->WriteValue(TEXT("pure"), Function->HasAnyFunctionFlags(FUNC_BlueprintPure));
		Writer->WriteValue(TEXT("static"), Function->HasAnyFunctionFlags(FUNC_Static));
		Writer->WriteValue(TEXT("const"), Function->HasAnyFunctionFlags(FUNC_Const));
		Writer->WriteValue(TEXT("callable"), Function->HasAnyFunctionFlags(FUNC_BlueprintCallable));
		Writer->WriteValue(TEXT("access"), AccessOf(Function));
		Writer->WriteValue(TEXT("net"), NetModeOf(Function));
		Writer->WriteValue(TEXT("nodes"), FunctionNodes.FindRef(Function->GetFName()));
		Writer->WriteValue(TEXT("interface"), InterfaceOf.FindRef(Function->GetFName()));
		WriteParams(Writer, Function);
		if (bBodies)
		{
			WriteBody(Writer, FGraphBody::FindEntryNode(FunctionGraphs.FindRef(Function->GetFName())));
		}
		Writer->WriteObjectEnd();
	}
	Writer->WriteArrayEnd();

	Writer->WriteArrayStart(TEXT("events"));
	for (const UFunction* Function : Events)
	{
		// A name the parent already declares is an override, a name an implemented interface
		// declares is an interface event, and anything else is a custom event.
		const bool bOverride = Blueprint->ParentClass != nullptr
			&& Blueprint->ParentClass->FindFunctionByName(Function->GetFName()) != nullptr;
		const bool bInterface = InterfaceOf.Contains(Function->GetFName());

		Writer->WriteObjectStart();
		Writer->WriteValue(TEXT("name"), Function->GetName());
		Writer->WriteValue(TEXT("kind"),
			bOverride ? TEXT("override") : bInterface ? TEXT("interface") : TEXT("custom"));
		Writer->WriteValue(TEXT("net"), NetModeOf(Function));
		Writer->WriteValue(TEXT("reliable"), Function->HasAnyFunctionFlags(FUNC_NetReliable));
		Writer->WriteValue(TEXT("access"), AccessOf(Function));
		WriteParams(Writer, Function);
		if (bBodies)
		{
			const UEdGraphNode* Node =
				FGraphBody::FindEventNode(Blueprint->UbergraphPages, Function->GetFName());
			Writer->WriteValue(TEXT("graph"),
				Node != nullptr && Node->GetGraph() != nullptr ? Node->GetGraph()->GetName() : FString());
			WriteBody(Writer, Node);
		}
		Writer->WriteObjectEnd();
	}
	Writer->WriteArrayEnd();

	Writer->WriteArrayStart(TEXT("dispatchers"));
	if (Generated != nullptr)
	{
		for (TFieldIterator<FMulticastDelegateProperty> It(Generated, EFieldIteratorFlags::ExcludeSuper); It; ++It)
		{
			Writer->WriteObjectStart();
			Writer->WriteValue(TEXT("name"), It->GetName());
			WriteParams(Writer, It->SignatureFunction);
			Writer->WriteObjectEnd();
		}
	}
	Writer->WriteArrayEnd();

	// A macro's signature lives in its tunnel nodes, which only the BlueprintGraph module can name.
	// The body starts at the tunnel that hands values out into it, which FindEntryNode resolves.
	Writer->WriteArrayStart(TEXT("macros"));
	for (UEdGraph* Graph : Blueprint->MacroGraphs)
	{
		if (Graph == nullptr)
		{
			continue;
		}
		Writer->WriteObjectStart();
		Writer->WriteValue(TEXT("name"), Graph->GetFName().ToString());
		Writer->WriteValue(TEXT("nodes"), MacroNodes.FindRef(Graph->GetFName()));
		if (bBodies)
		{
			WriteBody(Writer, FGraphBody::FindEntryNode(Graph));
		}
		Writer->WriteObjectEnd();
	}
	Writer->WriteArrayEnd();

	Writer->WriteObjectEnd();
	Writer->Close();

	return true;
}

bool FBlueprintSummary::WriteSummary(const FString& AssetPath, const FString& OutDir,
                                            bool bBodies, FString& OutFile, FString& OutError)
{
	OutFile.Empty();

	FString Json;
	if (!BuildSummary(AssetPath, bBodies, Json, OutError))
	{
		return false;
	}

	const FString Dir = OutDir.IsEmpty() ? FAssetTextExporter::DefaultOutDir() : OutDir;
	IFileManager::Get().MakeDirectory(*Dir, true);
	if (!IFileManager::Get().DirectoryExists(*Dir))
	{
		OutError = FString::Printf(TEXT("could not create '%s'"), *Dir);
		return false;
	}

	FString PackagePart;
	FString AssetPart;
	FAssetTextExporter::ToObjectPath(AssetPath).Split(TEXT("."), &PackagePart, &AssetPart);

	const FString Path = FPaths::ConvertRelativePathToFull(FPaths::Combine(Dir, AssetPart + TEXT(".json")));
	if (!FFileHelper::SaveStringToFile(Json, *Path, FFileHelper::EEncodingOptions::ForceUTF8))
	{
		OutError = FString::Printf(TEXT("could not write '%s'"), *Path);
		return false;
	}

	OutFile = Path;
	return true;
}
