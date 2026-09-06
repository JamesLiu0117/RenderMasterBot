#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterConversationAssistant.h"
#include "RenderMasterSha256.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterSha256KnownVectorTest,
    "RenderMasterBot.Editor.Evidence.Sha256KnownVectors",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterSha256KnownVectorTest::RunTest(const FString& Parameters)
{
    const ANSICHAR* Abc = "abc";
    FString Hash;
    TestTrue(
        TEXT("SHA-256 hashes the abc vector"),
        RenderMasterSha256Bytes(
            TConstArrayView<uint8>(
                reinterpret_cast<const uint8*>(Abc),
                3),
            Hash));
    TestEqual(
        TEXT("SHA-256 abc digest matches the standard vector"),
        Hash,
        FString(TEXT(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")));

    Hash.Reset();
    TestTrue(
        TEXT("SHA-256 hashes the empty vector"),
        RenderMasterSha256Bytes(TConstArrayView<uint8>(), Hash));
    TestEqual(
        TEXT("SHA-256 empty digest matches the standard vector"),
        Hash,
        FString(TEXT(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")));
    return !HasAnyErrors();
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterConversationTurnParseTest,
    "RenderMasterBot.Editor.Assistant.ConversationTurn",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterConversationTurnParseTest::RunTest(const FString& Parameters)
{
    const FString Path = FPaths::CreateTempFilename(
        *FPaths::ProjectIntermediateDir(),
        TEXT("conversation_turn_"),
        TEXT(".json"));
    const FString ValidJson = TEXT(R"JSON({
        "schema_version": "0.1",
        "conversation_id": "conversation_001",
        "turn_id": "turn_001",
        "request": "Move the selected rock upward.",
        "outcome": "route",
        "intent": "transform",
        "assistant_message": "I can prepare that Transform change.",
        "action_prompt": "Move the selected Actor upward by 100 cm in world space.",
        "clarification_question": null,
        "required_context": [],
        "context_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "model": "gpt-oss:20b",
        "attempt_count": 1,
        "recovery_reason": null
    })JSON");

    TestTrue(
        TEXT("Conversation turn fixture writes"),
        FFileHelper::SaveStringToFile(ValidJson, *Path));
    FRenderMasterConversationTurn Turn;
    FString Error;
    TestTrue(
        TEXT("Registered conversation turn parses"),
        RenderMasterParseConversationTurnFile(Path, Turn, Error));
    TestEqual(TEXT("Conversation ID"), Turn.ConversationId, FString(TEXT("conversation_001")));
    TestEqual(TEXT("Turn intent"), Turn.Intent, FString(TEXT("transform")));
    TestEqual(TEXT("Turn attempt count"), Turn.AttemptCount, 1);

    const FString UnsafeJson = ValidJson.Replace(
        TEXT("\"transform\""),
        TEXT("\"run_arbitrary_python\""));
    TestTrue(
        TEXT("Unsafe conversation fixture writes"),
        FFileHelper::SaveStringToFile(UnsafeJson, *Path));
    Error.Reset();
    TestFalse(
        TEXT("Unregistered action intent is rejected"),
        RenderMasterParseConversationTurnFile(Path, Turn, Error));
    TestTrue(TEXT("Unsafe route explains rejection"), !Error.IsEmpty());

    IFileManager::Get().Delete(*Path, false, true);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterConversationRequestWriteTest,
    "RenderMasterBot.Editor.Assistant.ConversationRequest",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterConversationRequestWriteTest::RunTest(const FString& Parameters)
{
    const FString Path = FPaths::CreateTempFilename(
        *FPaths::ProjectIntermediateDir(),
        TEXT("conversation_request_"),
        TEXT(".json"));
    FRenderMasterConversationContext Context;
    Context.ProjectName = TEXT("OptimizationPlugin");
    Context.LevelPath = TEXT("/Game/Maps/PerformanceTest");

    FRenderMasterConversationResultEvent Event;
    Event.EventId = TEXT("result_000001");
    Event.ConversationId = TEXT("conversation_001");
    Event.SourceTurnId = TEXT("turn_000001");
    Event.Intent = TEXT("transform");
    Event.Phase = TEXT("proposal");
    Event.Status = TEXT("needs_review");
    Event.Summary = TEXT(
        "Transform proposal transform_001 is ready and has not been applied.");
    Event.ProposalId = TEXT("transform_001");
    Event.SubjectIds.Add(
        TEXT("/Game/Maps/PerformanceTest:PersistentLevel.RockA"));
    Event.CreatedAtUtc = TEXT("2026-09-05T12:00:00Z");

    FString Error;
    TestTrue(
        TEXT("Conversation request with one tool result writes"),
        RenderMasterWriteConversationRequestFile(
            Path,
            TEXT("conversation_001"),
            TEXT("turn_000002"),
            TEXT("Was that Transform already applied?"),
            Context,
            TArray<FRenderMasterConversationMessage>(),
            {Event},
            Error));

    FString Text;
    TSharedPtr<FJsonObject> Json;
    TestTrue(
        TEXT("Conversation request fixture reads"),
        FFileHelper::LoadFileToString(Text, *Path));
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
    TestTrue(
        TEXT("Conversation request is valid JSON"),
        FJsonSerializer::Deserialize(Reader, Json) && Json.IsValid());
    const TArray<TSharedPtr<FJsonValue>>* Results = nullptr;
    TestTrue(
        TEXT("Conversation request exposes result_events"),
        Json.IsValid()
            && Json->TryGetArrayField(TEXT("result_events"), Results)
            && Results != nullptr
            && Results->Num() == 1);
    if (Results != nullptr && Results->Num() == 1)
    {
        const TSharedPtr<FJsonObject> ResultJson = (*Results)[0]->AsObject();
        TestEqual(
            TEXT("Serialized tool-result status"),
            ResultJson->GetStringField(TEXT("status")),
            FString(TEXT("needs_review")));
        TestEqual(
            TEXT("Serialized proposal identity"),
            ResultJson->GetStringField(TEXT("proposal_id")),
            FString(TEXT("transform_001")));
    }

    Event.ConversationId = TEXT("conversation_other");
    Error.Reset();
    TestFalse(
        TEXT("A result from another conversation is rejected"),
        RenderMasterWriteConversationRequestFile(
            Path,
            TEXT("conversation_001"),
            TEXT("turn_000002"),
            TEXT("Was that Transform already applied?"),
            Context,
            TArray<FRenderMasterConversationMessage>(),
            {Event},
            Error));
    TestTrue(TEXT("Rejected request explains the failure"), !Error.IsEmpty());

    Event.ConversationId = TEXT("conversation_001");
    Event.Status = TEXT("applied");
    Error.Reset();
    TestFalse(
        TEXT("A proposal event cannot claim applied status"),
        RenderMasterWriteConversationRequestFile(
            Path,
            TEXT("conversation_001"),
            TEXT("turn_000002"),
            TEXT("Was that Transform already applied?"),
            Context,
            TArray<FRenderMasterConversationMessage>(),
            {Event},
            Error));

    IFileManager::Get().Delete(*Path, false, true);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FRenderMasterConversationSessionRoundTripTest,
    "RenderMasterBot.Editor.Assistant.ConversationSession",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRenderMasterConversationSessionRoundTripTest::RunTest(
    const FString& Parameters)
{
    const FString Path = FPaths::CreateTempFilename(
        *FPaths::ProjectIntermediateDir(),
        TEXT("conversation_session_"),
        TEXT(".json"));

    FRenderMasterConversationMessage Message;
    Message.MessageId = TEXT("message_000001");
    Message.Role = TEXT("assistant");
    Message.Content = TEXT("The proposal is ready for review.");

    FRenderMasterConversationEvidenceReference Evidence;
    Evidence.EvidenceId = TEXT("result_evidence_001");
    Evidence.Kind = TEXT("proposal");
    Evidence.Path = TEXT("C:/Runtime/transform_001.json");
    Evidence.Sha256 = TEXT(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");

    FRenderMasterConversationResultEvent Event;
    Event.EventId = TEXT("result_000001");
    Event.ConversationId = TEXT("conversation_001");
    Event.SourceTurnId = TEXT("turn_000001");
    Event.Intent = TEXT("transform");
    Event.Phase = TEXT("proposal");
    Event.Status = TEXT("needs_review");
    Event.Summary = Message.Content;
    Event.ProposalId = TEXT("transform_001");
    Event.SubjectIds.Add(
        TEXT("/Game/Maps/PerformanceTest:PersistentLevel.RockA"));
    Event.Evidence.Add(Evidence);
    Event.CreatedAtUtc = TEXT("2026-09-05T12:00:00Z");

    FRenderMasterConversationTaskState Task;
    Task.SourceTurnId = Event.SourceTurnId;
    Task.Intent = Event.Intent;
    Task.Status = TEXT("needs_review");
    Task.ProposalId = Event.ProposalId;
    Task.ProposalEvidence = Evidence;

    FString Error;
    TestTrue(
        TEXT("Bounded conversation session writes atomically"),
        RenderMasterWriteConversationSessionFile(
            Path,
            TEXT("conversation_001"),
            TEXT("OptimizationPlugin"),
            {Message},
            {Event},
            Task,
            Error));
    TestFalse(
        TEXT("Conversation session leaves no temporary file"),
        FPaths::FileExists(Path + TEXT(".tmp")));

    FString RestoredConversationId;
    TArray<FRenderMasterConversationMessage> RestoredHistory;
    TArray<FRenderMasterConversationResultEvent> RestoredResults;
    TOptional<FRenderMasterConversationTaskState> RestoredTask;
    Error.Reset();
    TestTrue(
        TEXT("Bounded conversation session restores"),
        RenderMasterLoadConversationSessionFile(
            Path,
            TEXT("OptimizationPlugin"),
            RestoredConversationId,
            RestoredHistory,
            RestoredResults,
            RestoredTask,
            Error));
    TestEqual(
        TEXT("Restored conversation identity"),
        RestoredConversationId,
        FString(TEXT("conversation_001")));
    TestEqual(TEXT("Restored message count"), RestoredHistory.Num(), 1);
    TestEqual(TEXT("Restored result count"), RestoredResults.Num(), 1);
    TestTrue(TEXT("Review task restored"), RestoredTask.IsSet());
    if (RestoredTask.IsSet())
    {
        TestEqual(
            TEXT("Restored proposal identity"),
            RestoredTask->ProposalId,
            FString(TEXT("transform_001")));
        TestEqual(
            TEXT("Restored proposal evidence hash"),
            RestoredTask->ProposalEvidence.Sha256,
            Evidence.Sha256);
    }

    Task.ProposalEvidence.Sha256 = TEXT(
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    Error.Reset();
    TestFalse(
        TEXT("Task evidence that differs from its result event is rejected"),
        RenderMasterWriteConversationSessionFile(
            Path,
            TEXT("conversation_001"),
            TEXT("OptimizationPlugin"),
            {Message},
            {Event},
            Task,
            Error));

    IFileManager::Get().Delete(*Path, false, true);
    return true;
}

#endif
