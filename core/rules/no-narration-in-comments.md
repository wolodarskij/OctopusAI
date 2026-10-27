# No narration in comments

*An optional rule, on by default in every project (Optional rules, in Global settings and in Project Settings), to every comment in source code, config and content files.*

**A comment describes the thing it is attached to: what it is, or what it does.** It never narrates the work: what the code used to be or used to do, what an earlier version or session changed, what was fixed, measured, checked or decided, and when. That history belongs in the commit message or the ticket. Delete such a comment when you find it.

A comment that states a present constraint the next editor needs stays: "the list stays sorted by id".

**An instruction or config file states the rule only.** A rule, a skill, a brief section, a template, an entry file, a hook message, a setting and a pack file never say who set the rule, when, or why it came to be: no "Set by the owner, <date>", no "Owner order, <date>", no "the owner's order, <date>". That record belongs in the ticket. A ticket file and its `## Owner's orders` section are records, and keep it.
