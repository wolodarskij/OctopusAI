#pragma once

#include "CoreMinimal.h"

class UEdGraph;
class UEdGraphNode;
class UEdGraphPin;

/** Turns a Blueprint graph into indented pseudocode.
 *
 *  It walks the execution chain from an entry node and prints one line for each statement, with the
 *  node's own title and the value of every input. A pure node is not a statement: it is printed
 *  inline, as the expression of the pin that pulls it. */
class ASSETTEXTEXPORT_API FGraphBody
{
public:
	/** The body of one graph, starting at Start. Each line carries its own indentation. */
	static void Render(const UEdGraphNode* Start, TArray<FString>& OutLines);

	/** The `K2Node_FunctionEntry` of a function graph, or null. */
	static UEdGraphNode* FindEntryNode(const UEdGraph* Graph);

	/** The event node named EventName anywhere in Graphs, or null.
	 *
	 *  It reads `CustomFunctionName` on a custom event and `EventReference.MemberName` on an
	 *  override, so it finds both kinds. */
	static UEdGraphNode* FindEventNode(const TArray<UEdGraph*>& Graphs, const FName& EventName);

	/** The graph the node belongs to. */
	static UEdGraph* GraphOf(const UEdGraphNode* Node);
};
