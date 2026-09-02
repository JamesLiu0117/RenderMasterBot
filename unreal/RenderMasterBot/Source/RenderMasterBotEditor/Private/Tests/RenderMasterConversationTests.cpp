#if WITH_DEV_AUTOMATION_TESTS

#include "Misc/AutomationTest.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RenderMasterConversationAssistant.h"

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

#endif
