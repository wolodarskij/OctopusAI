# Unreal Engine C++ Coding Style

*Applies to: `*.h`, `*.cpp`, `*.inl`. Class-prefix conventions (`A`/`U`/`F`/`E`/`I`/`T`, `b` booleans) are covered in `unreal-engine.md` — this file adds C++-specific style rules.*

- Use PascalCase for all function names, variables, and types
- Use `int32`, `uint8`, `float`; never `int` or `unsigned char`
- Use `FString`; never `std::string`
- Use `TArray`; never `std::vector`
- Use `TMap`; never `std::unordered_map`
- Use `MakeShared`/`TSharedPtr`; never `std::shared_ptr`
- Include the matching header first, then Engine headers, then project headers
- Use `#pragma once`; never include guards
- Always use `TEXT()` macro for string literals passed to UE APIs
