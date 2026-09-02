#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"

class FRenderMasterWorkflowController;

enum class ERenderMasterConversationState : uint8
{
    Ready,
    Thinking,
    Completed,
    Failed,
};

struct FRenderMasterConversationSelectionItem
{
    FString ActorName;
    FString ActorPath;
    FString ActorClass;
    FString ActorKind;
};

struct FRenderMasterConversationContext
{
    FString ProjectName;
    FString LevelPath;
    FString PlayMode = TEXT("editor");
    TArray<FRenderMasterConversationSelectionItem> Selection;
    bool bSelectionTruncated = false;
    bool bLightingRigReviewReady = false;
    bool bShadowOptimizationReady = false;
    bool bShadowVerificationReady = false;
    FString PendingActionIntent;
};

struct FRenderMasterConversationMessage
{
    FString MessageId;
    FString Role;
    FString Content;
};

struct FRenderMasterConversationTurn
{
    FString ConversationId;
    FString TurnId;
    FString Request;
    FString Outcome;
    FString Intent;
    FString AssistantMessage;
    FString ActionPrompt;
    FString ClarificationQuestion;
    TArray<FString> RequiredContext;
    FString ContextSha256;
    FString Model;
    int32 AttemptCount = 0;
};

bool RenderMasterParseConversationTurnFile(
    const FString& Filename,
    FRenderMasterConversationTurn& OutTurn,
    FString& OutError);

DECLARE_DELEGATE_OneParam(
    FOnRenderMasterConversationTurnCompleted,
    const FRenderMasterConversationTurn&);
DECLARE_DELEGATE_OneParam(
    FOnRenderMasterConversationTurnFailed,
    const FString&);

class FRenderMasterConversationAssistant
    : public TSharedFromThis<FRenderMasterConversationAssistant>
{
public:
    explicit FRenderMasterConversationAssistant(
        TSharedPtr<FRenderMasterWorkflowController> InWorkflowController);
    ~FRenderMasterConversationAssistant();

    void Initialize();
    void Shutdown();
    bool StartTurn(
        const FString& Request,
        const FRenderMasterConversationContext& Context,
        const TArray<FRenderMasterConversationMessage>& History);
    void Cancel();
    void ResetConversation();

    bool CanStart() const;
    bool IsThinking() const { return State == ERenderMasterConversationState::Thinking; }
    ERenderMasterConversationState GetState() const { return State; }
    const FString& GetConversationId() const { return ConversationId; }
    FText GetStateText() const;

    void SetOnCompleted(FOnRenderMasterConversationTurnCompleted InDelegate)
    {
        OnCompleted = MoveTemp(InDelegate);
    }
    void SetOnFailed(FOnRenderMasterConversationTurnFailed InDelegate)
    {
        OnFailed = MoveTemp(InDelegate);
    }

private:
    bool Tick(float DeltaTime);
    bool WriteRequest(
        const FString& Filename,
        const FString& TurnId,
        const FString& Request,
        const FRenderMasterConversationContext& Context,
        const TArray<FRenderMasterConversationMessage>& History,
        FString& OutError) const;
    void CompleteProcess();
    void CloseProcessResources();
    void AppendLog(const FString& Line);
    void Fail(const FString& Error);

    TSharedPtr<FRenderMasterWorkflowController> WorkflowController;
    ERenderMasterConversationState State = ERenderMasterConversationState::Ready;
    FString ConversationId;
    FString ActiveTurnId;
    FString ActiveRequest;
    FString TurnOutputPath;
    FString ErrorText;
    FString ProcessLog;
    FProcHandle ProcessHandle;
    void* StdOutRead = nullptr;
    void* StdOutWrite = nullptr;
    void* StdErrRead = nullptr;
    void* StdErrWrite = nullptr;
    FTSTicker::FDelegateHandle TickHandle;
    FOnRenderMasterConversationTurnCompleted OnCompleted;
    FOnRenderMasterConversationTurnFailed OnFailed;
};
