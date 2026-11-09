# UE Reflection Macros

*Applies to: C++ headers (`*.h`). AngelScript uses the same `UPROPERTY`/`UFUNCTION` specifier syntax without `GENERATED_BODY()`; see the AngelScript rules, when the project uses AngelScript.*

## UCLASS()
Declares a class to the UE reflection system. Must appear before the class declaration.
```cpp
UCLASS(BlueprintType, Blueprintable)
class MYGAME_API AMyActor : public AActor
{
    GENERATED_BODY()
public:
    AMyActor();
};
```
Common specifiers: `BlueprintType`, `Blueprintable`, `Abstract`, `NotBlueprintable`, `ClassGroup`, `meta=(DisplayName="...")`

## UPROPERTY()
Exposes a property to the reflection system. Must appear before the member variable.
```cpp
UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Combat")
float Health = 100.0f;
```
Specifier meanings: `unreal-engine.md`, "UPROPERTY and UFUNCTION". Additionally here:
`VisibleAnywhere`, `meta=(ClampMin, ClampMax)`.

## UFUNCTION()
Exposes a function to the reflection system.
```cpp
UFUNCTION(BlueprintCallable, Category = "Combat")
void TakeDamage(float DamageAmount);

UFUNCTION(BlueprintImplementableEvent)
void OnDeath();
```
Specifier meanings: `unreal-engine.md`, "UPROPERTY and UFUNCTION".

## USTRUCT()
```cpp
USTRUCT(BlueprintType)
struct FMyStruct
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere)
    float Value;
};
```

## UENUM()
```cpp
UENUM(BlueprintType)
enum class EMyEnum : uint8
{
    None,
    Option1,
    Option2
};
```

## GENERATED_BODY()
Must be the first line inside every UCLASS/USTRUCT. Do not add anything before it.
