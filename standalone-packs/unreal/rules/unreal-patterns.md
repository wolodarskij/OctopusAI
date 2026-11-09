# Common UE Patterns

*Applies to: the C++ modules under `Source/`. For AngelScript equivalents, see the AngelScript rules when the project uses AngelScript, and https://angelscript.hazelight.se/.*

## Delegates

**C++ only. `UPROPERTY(BlueprintAssignable)` fails preprocessing in AngelScript and stops the hot reload of the
whole script layer** — the AngelScript rules have the form that works there.

```cpp
// Declaration (in header)
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnHealthChanged, float, NewHealth);

// Usage (as UPROPERTY)
UPROPERTY(BlueprintAssignable)
FOnHealthChanged OnHealthChanged;

// Broadcasting
OnHealthChanged.Broadcast(CurrentHealth);
```

## Timers
```cpp
GetWorldTimerManager().SetTimer(TimerHandle, this, &AMyActor::DoSomething, 2.0f, false);
GetWorldTimerManager().ClearTimer(TimerHandle);
```

## Subsystems
```cpp
UMySubsystem* Sub = GetGameInstance()->GetSubsystem<UMySubsystem>();
UMyLocalPlayerSubsystem* Sub = ULocalPlayer::GetSubsystem<UMyLocalPlayerSubsystem>(LocalPlayer);
```

## Enhanced Input (C++-side only)

Applies only if InputAction assets are adopted — see `unreal-engine.md` and read `DefaultInput.ini` to find what the project binds.

```cpp
// In SetupPlayerInputComponent:
UEnhancedInputComponent* EnhancedInput = CastChecked<UEnhancedInputComponent>(InputComponent);
EnhancedInput->BindAction(MoveAction, ETriggerEvent::Triggered, this, &AMyCharacter::Move);
```

## Logging
```cpp
// Declare in header:
DECLARE_LOG_CATEGORY_EXTERN(LogMyGame, Log, All);
// Define in cpp:
DEFINE_LOG_CATEGORY(LogMyGame);
// Usage:
UE_LOG(LogMyGame, Warning, TEXT("Player health: %f"), Health);
```

## Spawning Actors
```cpp
FActorSpawnParameters SpawnParams;
SpawnParams.Owner = this;
AMyActor* Actor = GetWorld()->SpawnActor<AMyActor>(ActorClass, SpawnLocation, SpawnRotation, SpawnParams);
```
