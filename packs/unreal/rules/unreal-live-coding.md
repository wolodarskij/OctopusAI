# Live Coding Limitations

*Applies to: C++ files under `Source/`. Script layers that hot-reload on save (for example AngelScript) do not need Live Coding.*

Live Coding (Ctrl+Alt+F11) patches binaries at runtime. These operations are NOT supported during Live Coding:

## Cannot Do
- Add or remove UPROPERTY members
- Add or remove UFUNCTION methods
- Change the signature (return type, name, parameters) of a UFUNCTION
- Add or remove UCLASS/USTRUCT/UENUM declarations
- Change class inheritance hierarchy
- Modify constructors of UObject-derived classes

## Can Do
- Modify function body logic
- Change local variables
- Modify non-reflected (non-UPROPERTY) member variables
- Add/modify non-reflected helper functions
- Change constants and literals

## Best Practice
If you need to add or remove reflected members, run a full build with
`{{OCTOPUS}}/packs/unreal/tools/build_editor.ps1`; Live Coding cannot patch them. While an editor
has the project open it builds nothing and leaves `.octopus/build-pending`, and `start_editor.ps1`
builds the next time the editor is down. Never close a live shared editor for a build.
