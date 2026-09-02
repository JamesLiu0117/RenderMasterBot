#include "SRenderMasterWorkspace.h"

#include "Editor.h"
#include "Camera/CameraActor.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Selection.h"
#include "Engine/Light.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "RenderMasterCameraAssistant.h"
#include "RenderMasterCameraBatchAssistant.h"
#include "RenderMasterPerformanceAssistant.h"
#include "RenderMasterLevelAuditAssistant.h"
#include "RenderMasterRuntimePerformanceAssistant.h"
#include "RenderMasterInsightsGpuAssistant.h"
#include "RenderMasterLevelProfilingAssistant.h"
#include "RenderMasterShadowOptimizationVerificationAssistant.h"
#include "RenderMasterWorkflowController.h"
#include "RenderMasterMaterialAssistant.h"
#include "RenderMasterLightAssistant.h"
#include "RenderMasterLightingRigAssistant.h"
#include "RenderMasterLightingRigReviewAssistant.h"
#include "RenderMasterTransformAssistant.h"
#include "SRenderMasterPanel.h"
#include "Styling/AppStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SComboButton.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Input/SNumericEntryBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "RenderMasterBotWorkspace"

namespace
{
constexpr float WorkspacePadding = 22.0f;

TSharedRef<SWidget> WorkspaceSectionTitle(
    TAttribute<FText> Title,
    TAttribute<FText> Subtitle)
{
    return SNew(SVerticalBox)
        + SVerticalBox::Slot().AutoHeight()
        [
            SNew(STextBlock)
            .Text(Title)
            .Font(FAppStyle::GetFontStyle(TEXT("HeadingMedium")))
        ]
        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 3.0f, 0.0f, 0.0f)
        [
            SNew(STextBlock)
            .Text(Subtitle)
            .ColorAndOpacity(FSlateColor::UseSubduedForeground())
            .AutoWrapText(true)
        ];
}
}

void SRenderMasterWorkspace::Construct(const FArguments& InArgs)
{
    Controller = InArgs._Controller;
    check(Controller.IsValid());
    ConversationAssistant = MakeShared<FRenderMasterConversationAssistant>(Controller);
    ConversationAssistant->SetOnCompleted(
        FOnRenderMasterConversationTurnCompleted::CreateSP(
            this,
            &SRenderMasterWorkspace::HandleConversationTurnCompleted));
    ConversationAssistant->SetOnFailed(
        FOnRenderMasterConversationTurnFailed::CreateSP(
            this,
            &SRenderMasterWorkspace::HandleConversationTurnFailed));
    ConversationAssistant->Initialize();
    MaterialAssistant = MakeShared<FRenderMasterMaterialAssistant>(Controller);
    MaterialAssistant->Initialize();
    TransformAssistant = MakeShared<FRenderMasterTransformBatchAssistant>(Controller);
    TransformAssistant->Initialize();
    LightAssistant = MakeShared<FRenderMasterLightBatchAssistant>(Controller);
    LightAssistant->Initialize();
    LightingRigAssistant = MakeShared<FRenderMasterLightingRigAssistant>(Controller);
    LightingRigAssistant->Initialize();
    LightingRigReviewAssistant = MakeShared<FRenderMasterLightingRigReviewAssistant>(
        Controller, LightingRigAssistant);
    LightingRigReviewAssistant->Initialize();
    CameraAssistant = MakeShared<FRenderMasterCameraAssistant>(Controller);
    CameraAssistant->Initialize();
    CameraBatchAssistant = MakeShared<FRenderMasterCameraBatchAssistant>(Controller);
    CameraBatchAssistant->Initialize();
    PerformanceAssistant = MakeShared<FRenderMasterPerformanceAssistant>(Controller);
    PerformanceAssistant->Initialize();
    LevelAuditAssistant = MakeShared<FRenderMasterLevelAuditAssistant>(Controller);
    LevelAuditAssistant->Initialize();
    RuntimePerformanceAssistant =
        MakeShared<FRenderMasterRuntimePerformanceAssistant>(Controller);
    RuntimePerformanceAssistant->Initialize();
    InsightsGpuAssistant = MakeShared<FRenderMasterInsightsGpuAssistant>(Controller);
    InsightsGpuAssistant->Initialize();
    LevelProfilingAssistant = MakeShared<FRenderMasterLevelProfilingAssistant>(
        Controller,
        LevelAuditAssistant,
        RuntimePerformanceAssistant,
        InsightsGpuAssistant);
    LevelProfilingAssistant->Initialize();
    ShadowVerificationAssistant =
        MakeShared<FRenderMasterShadowOptimizationVerificationAssistant>(
            Controller, LevelProfilingAssistant);
    ShadowVerificationAssistant->Initialize();
    AssistantReply = TEXT("Tell me what you want to understand or change. I will inspect the current Unreal context before proposing an action.");
    AddConversationMessage(
        TEXT("assistant"),
        TEXT("Tell me what you want to understand, analyze, or change. I can ask for missing context, answer basic questions, or route an approved capability from this conversation."));

    ChildSlot
    [
        SNew(SHorizontalBox)
        + SHorizontalBox::Slot().AutoWidth()
        [
            BuildNavigation()
        ]
        + SHorizontalBox::Slot().FillWidth(1.0f)
        [
            SAssignNew(PageSwitcher, SWidgetSwitcher)
            .WidgetIndex(0)
            + SWidgetSwitcher::Slot()
            [
                BuildAssistantPage()
            ]
            + SWidgetSwitcher::Slot()
            [
                SNew(SRenderMasterPanel)
                .Controller(Controller)
            ]
        ]
    ];
    RefreshConversationMessages();
}

SRenderMasterWorkspace::~SRenderMasterWorkspace()
{
    if (ConversationAssistant.IsValid())
    {
        ConversationAssistant->Shutdown();
        ConversationAssistant.Reset();
    }
    if (ShadowVerificationAssistant.IsValid())
    {
        ShadowVerificationAssistant->Shutdown();
        ShadowVerificationAssistant.Reset();
    }
    if (LevelProfilingAssistant.IsValid())
    {
        LevelProfilingAssistant->Shutdown();
        LevelProfilingAssistant.Reset();
    }
    if (InsightsGpuAssistant.IsValid())
    {
        InsightsGpuAssistant->Shutdown();
        InsightsGpuAssistant.Reset();
    }
    if (RuntimePerformanceAssistant.IsValid())
    {
        RuntimePerformanceAssistant->Shutdown();
        RuntimePerformanceAssistant.Reset();
    }
    if (LevelAuditAssistant.IsValid())
    {
        LevelAuditAssistant->Shutdown();
        LevelAuditAssistant.Reset();
    }
    if (PerformanceAssistant.IsValid())
    {
        PerformanceAssistant->Shutdown();
        PerformanceAssistant.Reset();
    }
    if (CameraBatchAssistant.IsValid())
    {
        CameraBatchAssistant->Shutdown();
        CameraBatchAssistant.Reset();
    }
    if (LightingRigReviewAssistant.IsValid())
    {
        LightingRigReviewAssistant->Shutdown();
        LightingRigReviewAssistant.Reset();
    }
    if (LightingRigAssistant.IsValid())
    {
        LightingRigAssistant->Shutdown();
        LightingRigAssistant.Reset();
    }
    if (CameraAssistant.IsValid())
    {
        CameraAssistant->Shutdown();
        CameraAssistant.Reset();
    }
    if (LightAssistant.IsValid())
    {
        LightAssistant->Shutdown();
        LightAssistant.Reset();
    }
    if (TransformAssistant.IsValid())
    {
        TransformAssistant->Shutdown();
        TransformAssistant.Reset();
    }
    if (MaterialAssistant.IsValid())
    {
        MaterialAssistant->Shutdown();
        MaterialAssistant.Reset();
    }
}

TSharedRef<SWidget> SRenderMasterWorkspace::BuildNavigation()
{
    return SNew(SBox)
        .WidthOverride(208.0f)
        [
            SNew(SBorder)
            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
            .Padding(FMargin(12.0f, 18.0f))
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot().AutoHeight().Padding(8.0f, 0.0f, 8.0f, 4.0f)
                [
                    SNew(STextBlock)
                    .Text(LOCTEXT("ProductName", "RenderMasterBot"))
                    .Font(FAppStyle::GetFontStyle(TEXT("HeadingMedium")))
                ]
                + SVerticalBox::Slot().AutoHeight().Padding(8.0f, 0.0f, 8.0f, 18.0f)
                [
                    SNew(STextBlock)
                    .Text(LOCTEXT("ProductType", "Graphics Assistant"))
                    .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                ]
                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 2.0f)
                [
                    MakeNavigationButton(LOCTEXT("AssistantNav", "Assistant"), 0)
                ]
                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 2.0f)
                [
                    MakeNavigationButton(LOCTEXT("RenderNav", "Render & Evaluate"), 1)
                ]
                + SVerticalBox::Slot().FillHeight(1.0f)
                + SVerticalBox::Slot().AutoHeight().Padding(8.0f, 10.0f)
                [
                    SNew(SVerticalBox)
                    + SVerticalBox::Slot().AutoHeight()
                    [
                        SNew(STextBlock)
                        .Text(LOCTEXT("LocalModels", "LOCAL WORKSPACE"))
                        .Font(FAppStyle::GetFontStyle(TEXT("SmallFontBold")))
                        .ColorAndOpacity(FLinearColor(0.22f, 0.67f, 1.0f))
                    ]
                    + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 5.0f, 0.0f, 0.0f)
                    [
                        SNew(STextBlock)
                        .Text(LOCTEXT("LocalPrivacy", "Models, project assets, prompts, and render evidence stay on this workstation."))
                        .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                        .AutoWrapText(true)
                    ]
                ]
            ]
        ];
}

TSharedRef<SWidget> SRenderMasterWorkspace::BuildAssistantPage()
{
    return SNew(SBorder)
        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Background")))
        .Padding(0.0f)
        [
            SNew(SScrollBox)
            + SScrollBox::Slot()
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot().AutoHeight().Padding(WorkspacePadding, 20.0f, WorkspacePadding, 14.0f)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot().FillWidth(1.0f)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(STextBlock)
                            .Text(LOCTEXT("AssistantTitle", "Graphics Assistant"))
                            .Font(FAppStyle::GetFontStyle(TEXT("HeadingExtraLarge")))
                        ]
                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 4.0f, 0.0f, 0.0f)
                        [
                            SNew(STextBlock)
                            .Text(LOCTEXT("AssistantSubtitle", "Understand the current Unreal context, review a bounded action, then decide whether to execute."))
                            .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                        ]
                    ]
                    + SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
                    [
                        SNew(SBorder)
                        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Header")))
                        .BorderBackgroundColor_Lambda([Controller = Controller]() { return Controller->GetStatusColor(); })
                        .Padding(FMargin(12.0f, 7.0f))
                        [
                            SNew(STextBlock)
                            .Text_Lambda([Controller = Controller]() { return Controller->GetStatusText(); })
                            .Font(FAppStyle::GetFontStyle(TEXT("SmallFontBold")))
                        ]
                    ]
                ]

                + SVerticalBox::Slot().AutoHeight().Padding(WorkspacePadding, 0.0f, WorkspacePadding, WorkspacePadding)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot().FillWidth(0.62f).Padding(0.0f, 0.0f, 8.0f, 0.0f)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("Conversation", "Conversation"), LOCTEXT("ConversationHelp", "Context inspection is read-only. Every executable action must be approved."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SBox).HeightOverride(300.0f)
                                    [
                                        SAssignNew(ConversationScrollBox, SScrollBox)
                                        + SScrollBox::Slot()
                                        [
                                            SAssignNew(ConversationMessagesBox, SVerticalBox)
                                        ]
                                        + SScrollBox::Slot().Padding(0.0f, 4.0f, 0.0f, 8.0f)
                                        [
                                            MakeMessage(
                                                LOCTEXT("CurrentActivitySpeaker", "CURRENT ACTIVITY"),
                                                TAttribute<FText>::CreateSP(this, &SRenderMasterWorkspace::GetAssistantReply),
                                                FLinearColor(0.12f, 0.68f, 0.48f))
                                        ]
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetShadowVerificationVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = ShadowVerificationAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("ShadowVerificationTitle", "Post-Apply Shadow Verification"), TAttribute<FText>::CreateLambda([Assistant = ShadowVerificationAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = ShadowVerificationAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .Visibility(this, &SRenderMasterWorkspace::GetShadowVerificationStartVisibility)
                                    .IsEnabled_Lambda([Assistant = ShadowVerificationAssistant]() { return Assistant->CanStartVerification(); })
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("RunShadowVerification", "Run Post-Apply Verification"))
                                    .ToolTipText(LOCTEXT("RunShadowVerificationHelp", "Restart the same representative PIE/SIE workload first. The Assistant repeats the complete profiling chain and compares fresh whole-level evidence with fixed regression guards."))
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareShadowVerificationAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 8.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetShadowVerificationKeepVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text_Lambda([Assistant = ShadowVerificationAssistant]()
                                        {
                                            return Assistant->GetReport().Decision == TEXT("retain")
                                                ? LOCTEXT("KeepVerifiedShadowOptimization", "Keep Optimization")
                                                : LOCTEXT("KeepShadowOptimizationOverride", "Keep Anyway (Override Recommendation)");
                                        })
                                        .ToolTipText(LOCTEXT("KeepShadowOptimizationHelp", "Keep the current unsaved formal change. This performs no scene mutation and does not convert an unresolved or rollback result into a performance success claim."))
                                        .OnClicked(this, &SRenderMasterWorkspace::KeepShadowOptimizationAction)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetShadowVerificationRollbackVisibility)
                                        .Text(LOCTEXT("RollbackShadowOptimization", "Approve Rollback"))
                                        .ToolTipText(LOCTEXT("RollbackShadowOptimizationHelp", "Stop PIE/SIE first. Revalidate the exact formal target, then restore Cast Shadow false to true as a new unsaved Undo transaction."))
                                        .OnClicked(this, &SRenderMasterWorkspace::RollbackShadowOptimizationAction)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 8.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .Visibility(this, &SRenderMasterWorkspace::GetShadowVerificationDismissVisibility)
                                    .Text_Lambda([Assistant = ShadowVerificationAssistant]()
                                    {
                                        return Assistant->IsBusy()
                                            ? LOCTEXT("CancelShadowVerification", "Cancel Verification")
                                            : LOCTEXT("DismissShadowVerification", "Dismiss Verification");
                                    })
                                    .OnClicked(this, &SRenderMasterWorkspace::DismissShadowVerificationAction)
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetLevelProfilingVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = LevelProfilingAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("LevelProfilingTitle", "Profiling-First Level Audit"), TAttribute<FText>::CreateLambda([Assistant = LevelProfilingAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = LevelProfilingAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .Visibility(this, &SRenderMasterWorkspace::GetLevelProfilingDismissVisibility)
                                    .Text_Lambda([Assistant = LevelProfilingAssistant]()
                                    {
                                        return Assistant->IsBusy()
                                            ? LOCTEXT("CancelLevelProfiling", "Cancel Profiling Audit")
                                            : LOCTEXT("DismissLevelProfiling", "Dismiss Profiling Report");
                                    })
                                    .OnClicked(this, &SRenderMasterWorkspace::DismissLevelProfilingAction)
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetInsightsGpuVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = InsightsGpuAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(TAttribute<FText>::CreateLambda([Assistant = InsightsGpuAssistant]() { return Assistant->GetTitleText(); }), TAttribute<FText>::CreateLambda([Assistant = InsightsGpuAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = InsightsGpuAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetShadowOptimizationApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveShadowOptimization", "Approve & Apply Shadow Optimization"))
                                        .ToolTipText(LOCTEXT("ApproveShadowOptimizationHelp", "Stop PIE/SIE first. Revalidate the exact formal Actor, Component, Mesh, and Before state, then apply only Cast Shadow true to false as one unsaved Undo transaction."))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveShadowOptimizationAction)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetShadowOptimizationRejectVisibility)
                                        .Text(LOCTEXT("RejectShadowOptimization", "Reject Shadow Optimization"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectShadowOptimizationAction)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 8.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetInsightsGpuBaselineOpenVisibility)
                                        .Text_Lambda([Assistant = InsightsGpuAssistant]()
                                        {
                                            return Assistant->IsShadowOptimizationStudy()
                                                ? LOCTEXT("OpenShadowOptimizationControlTrace", "Open First Shadow-On Control Trace")
                                                : Assistant->IsRepeatableActorImpactStudy()
                                                ? LOCTEXT("OpenActorGpuStudyControlTrace", "Open First A/A Control Trace")
                                                : LOCTEXT("OpenActorGpuBaselineTrace", "Open Baseline Trace");
                                        })
                                        .OnClicked(this, &SRenderMasterWorkspace::OpenInsightsGpuBaselineTraceAction)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetInsightsGpuOpenVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text_Lambda([Assistant = InsightsGpuAssistant]()
                                        {
                                            return Assistant->IsShadowOptimizationStudy()
                                                ? LOCTEXT("OpenShadowOptimizationCandidateTrace", "Open Final Shadow-Off Trace")
                                                : Assistant->IsActorImpactExperiment()
                                                ? Assistant->IsRepeatableActorImpactStudy()
                                                    ? LOCTEXT("OpenActorGpuStudyFinalTrace", "Open Final Hidden Trace")
                                                    : LOCTEXT("OpenActorGpuVariantTrace", "Open Actor-Hidden Trace")
                                                : LOCTEXT("OpenInsightsGpuTrace", "Open Trace in Unreal Insights");
                                        })
                                        .OnClicked(this, &SRenderMasterWorkspace::OpenInsightsGpuTraceAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetInsightsGpuDismissVisibility)
                                        .Text(LOCTEXT("DismissInsightsGpu", "Dismiss Review"))
                                        .OnClicked(this, &SRenderMasterWorkspace::DismissInsightsGpuAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetRuntimePerformanceVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = RuntimePerformanceAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("RuntimePerformanceTitle", "Runtime Performance Capture"), TAttribute<FText>::CreateLambda([Assistant = RuntimePerformanceAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = RuntimePerformanceAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .Visibility(this, &SRenderMasterWorkspace::GetRuntimePerformanceDismissVisibility)
                                    .Text(LOCTEXT("DismissRuntimePerformance", "Dismiss Review"))
                                    .OnClicked(this, &SRenderMasterWorkspace::DismissRuntimePerformanceAction)
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetLevelAuditVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = LevelAuditAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("LevelAuditTitle", "Full Level Audit"), TAttribute<FText>::CreateLambda([Assistant = LevelAuditAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = LevelAuditAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLevelAuditApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveLevelAudit", "Approve & Apply Audit Actions"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveLevelAuditAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLevelAuditRejectVisibility)
                                        .Text(LOCTEXT("DismissLevelAudit", "Dismiss / Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectLevelAuditAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetPerformanceProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = PerformanceAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("PerformanceProposalTitle", "Selected Mesh Performance Review"), TAttribute<FText>::CreateLambda([Assistant = PerformanceAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = PerformanceAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetPerformanceApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApprovePerformance", "Approve & Apply Settings"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApprovePerformanceAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetPerformanceRejectVisibility)
                                        .Text(LOCTEXT("DismissPerformance", "Dismiss / Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectPerformanceAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetCameraBatchProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = CameraBatchAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("CameraBatchProposalTitle", "Coordinated Camera Action"), TAttribute<FText>::CreateLambda([Assistant = CameraBatchAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = CameraBatchAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetCameraBatchApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveCameraBatch", "Approve & Apply Cameras"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveCameraBatchAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetCameraBatchRejectVisibility)
                                        .Text(LOCTEXT("RejectCameraBatch", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectCameraBatchAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetLightingRigReviewVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = LightingRigReviewAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("LightingRigReviewTitle", "Lighting Rig Visual Review"), TAttribute<FText>::CreateLambda([Assistant = LightingRigReviewAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = LightingRigReviewAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightingRigReviewApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveLightingRigReview", "Approve Intensity Correction"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveLightingRigReviewAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightingRigReviewRejectVisibility)
                                        .Text(LOCTEXT("RejectLightingRigReview", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectLightingRigReviewAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [
                                    SNew(SBox).HeightOverride(135.0f)
                                    [
                                        SAssignNew(AssistantPromptBox, SMultiLineEditableTextBox)
                                        .HintText(LOCTEXT("AssistantPromptHint", "Ask about the current scene, or describe a change you want to make..."))
                                        .AutoWrapText(true)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 8.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("SendConversation", "Send to Assistant"))
                                        .ToolTipText(LOCTEXT("SendConversationHelp", "Let the local planner answer, ask a clarifying question, or route one registered bounded capability from the current Unreal context."))
                                        .IsEnabled(this, &SRenderMasterWorkspace::CanSendConversationMessage)
                                        .OnClicked(this, &SRenderMasterWorkspace::SendConversationMessage)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Text(LOCTEXT("ClearConversation", "New Conversation"))
                                        .ToolTipText(LOCTEXT("ClearConversationHelp", "Clear the local in-memory message timeline. This does not modify the Unreal project or delete preserved workflow evidence."))
                                        .IsEnabled_Lambda([Assistant = ConversationAssistant]() { return !Assistant->IsThinking(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::ClearConversation)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(SExpandableArea)
                                    .InitiallyCollapsed(true)
                                    .AreaTitle(LOCTEXT("AdvancedDirectTools", "Advanced Direct Tools"))
                                    .ToolTipText(LOCTEXT("AdvancedDirectToolsHelp", "Optional direct entry points for debugging or expert operation. Normal use starts with Send to Assistant."))
                                    .BodyContent()
                                    [
                                        SNew(SVerticalBox)
                                        + SVerticalBox::Slot().AutoHeight()
                                        [
                                            SNew(STextBlock)
                                            .Text(LOCTEXT("AdvancedDirectToolsDescription", "Use these controls only when you want to bypass conversational routing and choose an exact capability yourself."))
                                            .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                                            .AutoWrapText(true)
                                        ]
                                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                        [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.0f, 0.0f, 12.0f, 0.0f)
                                    [
                                        SNew(STextBlock)
                                        .Text(LOCTEXT("TargetMaterialSlot", "Target material slot"))
                                        .Font(FAppStyle::GetFontStyle(TEXT("SmallFontBold")))
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SAssignNew(MaterialSlotComboButton, SComboButton)
                                        .OnGetMenuContent(this, &SRenderMasterWorkspace::BuildMaterialSlotMenu)
                                        .ToolTipText(LOCTEXT("TargetMaterialSlotHelp", "Single-slot meshes are selected automatically. Multi-slot meshes require an explicit target."))
                                        .ButtonContent()
                                        [
                                            SNew(STextBlock)
                                            .Text(this, &SRenderMasterWorkspace::GetTargetMaterialSlotText)
                                        ]
                                    ]
                                        ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .Text(LOCTEXT("AnalyzeContext", "Analyze Context"))
                                        .OnClicked(this, &SRenderMasterWorkspace::AnalyzeContext)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .Text(LOCTEXT("SearchPolyHaven", "Search Poly Haven"))
                                        .ToolTipText(LOCTEXT("SearchPolyHavenHelp", "Search CC0 external materials and download a verified local cache. Unreal Content is changed only after a separate approval."))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareExternalAction)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("PrepareAction", "Prepare Material"))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareAction)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("PrepareTransform", "Prepare Transform"))
                                        .ToolTipText(LOCTEXT("PrepareTransformHelp", "Prepare one world- or local-space move, rotation, or scale proposal for one to 32 selected Actors."))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareTransformAction)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("PrepareLight", "Prepare Light"))
                                        .ToolTipText(LOCTEXT("PrepareLightHelp", "Prepare one compatible property proposal for one to 16 selected Directional, Point, Spot, or Rect Lights."))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareLightAction)
                                    ]
                                    + SHorizontalBox::Slot().FillWidth(1.0f)
                                    [
                                        SNew(SButton)
                                        .HAlign(HAlign_Center)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("PrepareCamera", "Prepare Camera"))
                                        .ToolTipText(LOCTEXT("PrepareCameraHelp", "Prepare bounded Transform, lens, focus, or exposure-compensation properties for one selected camera, or one coordinated action for 2-16 selected Camera/Cine Camera Actors."))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareCameraAction)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("RunLevelProfilingAudit", "Run Profiling-First Level Audit"))
                                    .ToolTipText(LOCTEXT("RunLevelProfilingAuditHelp", "During one fixed PIE/SIE workload, run the read-only static funnel, a 30 plus 120 frame baseline, a five-second Unreal Insights GPU trace, and up to two repeatable eight-capture Actor studies. Temporary runtime visibility is restored. Images are not used to claim performance."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareLevelProfilingAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("RunShadowOptimization", "Test Best Shadow Optimization"))
                                    .ToolTipText(LOCTEXT("RunShadowOptimizationHelp", "After a Profiling-First Level Audit finds a stable Actor candidate, automatically test Cast Shadow on versus off with two A/A controls and three counterbalanced pairs. Runtime state is restored. Only a measured win can produce an approval-gated formal change."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareShadowOptimizationAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("RunFullLevelAudit", "Run Full Level Audit"))
                                    .ToolTipText(LOCTEXT("RunFullLevelAuditHelp", "Inspect every eligible native StaticMeshActor in the current Editor level, compute deterministic static-risk priorities, and request a constrained local-model review. Optional Cast Shadow or Max Draw Distance actions remain approval-gated, stale-state checked, Undo-backed, and unsaved."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareLevelAuditAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("PrepareLightingRig", "Prepare Lighting Rig"))
                                    .ToolTipText(LOCTEXT("PrepareLightingRigHelp", "Select one bounded subject Actor, one perspective Camera/Cine Camera, and exactly three Movable Point/Spot/Rect Lights that share one non-EV intensity unit."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareLightingRigAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .Text(LOCTEXT("ReviewAppliedLightingRig", "Evaluate Applied Rig"))
                                    .ToolTipText(LOCTEXT("ReviewAppliedLightingRigHelp", "After applying a three-point rig, activate a perspective Level Editor viewport. The Assistant temporarily captures a Lit PNG from the frozen camera, restores the viewport, and asks the local vision model for one intensity-only correction."))
                                    .IsEnabled_Lambda([Assistant = LightingRigReviewAssistant]() { return Assistant->CanStart(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::ReviewAppliedLightingRigAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("ReviewPerformance", "Review Performance"))
                                    .ToolTipText(LOCTEXT("ReviewPerformanceHelp", "Capture measured mesh, LOD, material, Nanite, collision, Tick, shadow, culling, and bounds evidence for 1-32 selected StaticMeshActors. Only Cast Shadow and Max Draw Distance can be approval-gated in this milestone."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PreparePerformanceAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("CaptureRuntimePerformance", "Capture Runtime Performance"))
                                    .ToolTipText(LOCTEXT("CaptureRuntimePerformanceHelp", "During Play In Editor or Simulate In Editor, warm up 30 frames and capture 120 consecutive frame, Game Thread, Render Thread, optional RHI Thread, GPU, process-memory, and RHI texture-memory measurements. The AI review is read-only."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareRuntimePerformanceAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("CaptureInsightsGpu", "Capture GPU Scope Trace"))
                                    .ToolTipText(LOCTEXT("CaptureInsightsGpuHelp", "During PIE or SIE, record five seconds of CPU, GPU, frame, and bookmark trace channels, parse GPU queues with Unreal TraceServices, rank inclusive GPU scopes, preserve the .utrace file, and request a read-only local-model review."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareInsightsGpuAction)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 6.0f, 0.0f, 0.0f)
                                [
                                    SNew(SButton)
                                    .HAlign(HAlign_Center)
                                    .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                    .Text(LOCTEXT("MeasureActorGpuImpact", "Run Repeatable Selected Actor GPU Study"))
                                    .ToolTipText(LOCTEXT("MeasureActorGpuImpactHelp", "During PIE or SIE, run two visible A/A control captures and three counterbalanced visible/hidden pairs in AB, BA, AB order. Every capture has a fixed warmup; the runtime Actor is restored on completion, cancellation, or failure. The formal level is never changed or saved."))
                                    .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                    .OnClicked(this, &SRenderMasterWorkspace::PrepareActorGpuImpactAction)
                                ]
                                    ]
                                ]
                            ]
                        ]
                    ]

                    + SHorizontalBox::Slot().FillWidth(0.38f).Padding(8.0f, 0.0f, 0.0f, 0.0f)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetLightingRigProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = LightingRigAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("LightingRigProposalTitle", "Lighting Rig Action"), TAttribute<FText>::CreateLambda([Assistant = LightingRigAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = LightingRigAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightingRigApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveLightingRig", "Approve & Apply Rig"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveLightingRigAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightingRigRejectVisibility)
                                        .Text(LOCTEXT("RejectLightingRig", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectLightingRigAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("ContextTitle", "Current Unreal Context"), LOCTEXT("ContextHelp", "Live read-only context from this Editor session."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text(this, &SRenderMasterWorkspace::GetProjectContext)
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f)
                                [SNew(SSeparator)]
                                + SVerticalBox::Slot().AutoHeight()
                                [
                                    SNew(STextBlock)
                                    .Text(this, &SRenderMasterWorkspace::GetSelectionContext)
                                    .AutoWrapText(true)
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetCameraProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = CameraAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("CameraProposalTitle", "Camera Action"), TAttribute<FText>::CreateLambda([Assistant = CameraAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = CameraAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetCameraApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveCamera", "Approve & Apply Camera"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveCameraAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetCameraRejectVisibility)
                                        .Text(LOCTEXT("RejectCamera", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectCameraAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetLightProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = LightAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("LightProposalTitle", "Light Action"), TAttribute<FText>::CreateLambda([Assistant = LightAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = LightAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveLight", "Approve & Apply Light"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveLightAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetLightRejectVisibility)
                                        .Text(LOCTEXT("RejectLight", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectLightAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetTransformProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = TransformAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("TransformProposalTitle", "Transform Action"), TAttribute<FText>::CreateLambda([Assistant = TransformAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = TransformAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetTransformApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text(LOCTEXT("ApproveTransform", "Approve & Apply Transform"))
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveTransformAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetTransformRejectVisibility)
                                        .Text(LOCTEXT("RejectTransform", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectTransformAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .Visibility(this, &SRenderMasterWorkspace::GetProposalVisibility)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .BorderBackgroundColor_Lambda([Assistant = MaterialAssistant]() { return Assistant->GetStateColor(); })
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("ProposalTitle", "Material Action"), TAttribute<FText>::CreateLambda([Assistant = MaterialAssistant]() { return Assistant->GetStateText(); }))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Assistant = MaterialAssistant]() { return Assistant->GetSummaryText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetApplyVisibility)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .Text_Lambda([Assistant = MaterialAssistant]() { return Assistant->GetApprovalButtonText(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::ApproveAction)
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Visibility(this, &SRenderMasterWorkspace::GetRejectVisibility)
                                        .Text(LOCTEXT("Reject", "Reject"))
                                        .OnClicked(this, &SRenderMasterWorkspace::RejectAction)
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(16.0f)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()
                                [WorkspaceSectionTitle(LOCTEXT("Capabilities", "Connected Capabilities"), LOCTEXT("CapabilitiesHelp", "Only connected actions can be approved."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text(LOCTEXT("CapabilityList", "CONNECTED\n✓ Live project and actor-selection context\n✓ Explicit material-slot targeting\n✓ Catalog-backed project material recommendation\n✓ CC0 Poly Haven search and verified local cache\n✓ Hash-bound approval for 5-asset PBR import\n✓ Automatic catalog and Chroma synchronization\n✓ Approval-gated material override with Ctrl+Z Undo\n✓ Approval-gated 1–32 Actor world/local Transform with grouped Ctrl+Z Undo\n✓ Approval-gated 1–16 Light compatible group properties with grouped Ctrl+Z Undo\n✓ Subject- and camera-aware Key/Fill/Rim rig with grouped Ctrl+Z Undo\n✓ Camera-view visual rig review with approval-gated intensity correction\n✓ Camera and Cine Camera Transform, lens, focus, and exposure compensation with Ctrl+Z Undo\n✓ Coordinated 2–16 Camera editing with grouped Ctrl+Z Undo\n✓ Evidence-backed StaticMeshActor performance review\n✓ Approval-gated shadow and cull-distance changes with grouped Ctrl+Z Undo\n✓ Recomputable PIE/SIE frame, thread, GPU, process-memory, and RHI texture-memory review\n✓ Unreal Insights GPU queue and scope trace review\n✓ Repeatable selected-Actor AA + AB/BA/AB GPU study with automatic restoration\n✓ Profiling-first whole-level orchestration under one fixed PIE/SIE workload\n✓ Profiling-gated Cast Shadow A/B optimization with runtime restoration and approval-backed Undo\n✓ Post-apply whole-level verification with fixed retain/rollback guards\n✓ Schema-gated scene planning\n✓ Transient Unreal preview\n✓ Visual evaluation and bounded correction\n\nNOT CONNECTED YET\n○ Geometry-aware arrangement beyond the bounded three-point rig\n○ Direct per-draw/per-material/per-shader GPU attribution\n○ Packaged-build benchmark comparison\n○ Asset-level Nanite, LOD, mesh, material, or collision optimization"))
                                    .AutoWrapText(true)
                                    .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                                ]
                            ]
                        ]
                    ]
                ]
            ]
        ];
}

TSharedRef<SWidget> SRenderMasterWorkspace::BuildMaterialSlotMenu()
{
    FString Error;
    UStaticMeshComponent* Component = GetSingleSelectedStaticMeshComponent(Error);
    if (Component == nullptr || Component->GetStaticMesh() == nullptr)
    {
        return SNew(SBorder)
            .Padding(10.0f)
            [
                SNew(STextBlock)
                .Text(FText::FromString(Error))
                .AutoWrapText(true)
            ];
    }

    const TArray<FStaticMaterial>& Materials = Component->GetStaticMesh()->GetStaticMaterials();
    TSharedRef<SVerticalBox> Entries = SNew(SVerticalBox);
    const TWeakObjectPtr<UStaticMeshComponent> WeakComponent(Component);
    for (int32 Index = 0; Index < Materials.Num(); ++Index)
    {
        const FName SlotName = Materials[Index].MaterialSlotName;
        const FString Name = SlotName.IsNone()
            ? FString::Printf(TEXT("Material_%d"), Index)
            : SlotName.ToString();
        const UMaterialInterface* CurrentMaterial = Component->GetMaterial(Index);
        const FString CurrentPath = CurrentMaterial != nullptr
            ? CurrentMaterial->GetPathName()
            : TEXT("None");
        const FString Label = FString::Printf(
            TEXT("%d — %s\nCurrent: %s"),
            Index,
            *Name,
            *CurrentPath);

        Entries->AddSlot().AutoHeight().Padding(2.0f)
        [
            SNew(SButton)
            .ContentPadding(FMargin(10.0f, 7.0f))
            .HAlign(HAlign_Left)
            .Text(FText::FromString(Label))
            .OnClicked_Lambda([this, WeakComponent, Index]()
            {
                if (UStaticMeshComponent* CurrentComponent = WeakComponent.Get())
                {
                    SelectTargetMaterialSlot(CurrentComponent, Index);
                }
                if (MaterialSlotComboButton.IsValid())
                {
                    MaterialSlotComboButton->SetIsOpen(false);
                }
                return FReply::Handled();
            })
        ];
    }

    return SNew(SBox)
        .MinDesiredWidth(460.0f)
        .MaxDesiredHeight(360.0f)
        [
            SNew(SScrollBox)
            + SScrollBox::Slot()
            [
                Entries
            ]
        ];
}

TSharedRef<SWidget> SRenderMasterWorkspace::MakeNavigationButton(const FText& Label, int32 PageIndex)
{
    return SNew(SButton)
        .ButtonColorAndOpacity_Lambda([this, PageIndex]()
        {
            return ActivePage == PageIndex ? FLinearColor(0.08f, 0.38f, 0.68f) : FLinearColor(0.12f, 0.13f, 0.16f);
        })
        .ContentPadding(FMargin(12.0f, 9.0f))
        .OnClicked_Lambda([this, PageIndex]() { SetPage(PageIndex); return FReply::Handled(); })
        [
            SNew(STextBlock).Text(Label)
        ];
}

TSharedRef<SWidget> SRenderMasterWorkspace::MakeMessage(
    const FText& Speaker,
    TAttribute<FText> Message,
    const FLinearColor& Accent)
{
    return SNew(SBorder)
        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Header")))
        .BorderBackgroundColor(FLinearColor(Accent.R * 0.18f, Accent.G * 0.18f, Accent.B * 0.18f, 1.0f))
        .Padding(12.0f)
        [
            SNew(SVerticalBox)
            + SVerticalBox::Slot().AutoHeight()
            [
                SNew(STextBlock)
                .Text(Speaker)
                .Font(FAppStyle::GetFontStyle(TEXT("SmallFontBold")))
                .ColorAndOpacity(Accent)
            ]
            + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 5.0f, 0.0f, 0.0f)
            [
                SNew(STextBlock)
                .Text(Message)
                .AutoWrapText(true)
            ]
        ];
}

FReply SRenderMasterWorkspace::SendConversationMessage()
{
    if (!CanSendConversationMessage()) return FReply::Handled();
    const FString Request =
        AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    FRenderMasterConversationContext Context;
    FString Error;
    if (!CaptureConversationContext(Context, Error))
    {
        AssistantReply = Error;
        AddConversationMessage(TEXT("assistant"), Error);
        return FReply::Handled();
    }

    const TArray<FRenderMasterConversationMessage> PriorHistory =
        ConversationMessages;
    LastRequest = Request;
    LastAssistantAction = ELastAssistantAction::None;
    AddConversationMessage(TEXT("user"), Request);
    AssistantPromptBox->SetText(FText::GetEmpty());
    AssistantReply = TEXT(
        "I am reading the current Editor context and recent conversation, then deciding whether to answer, clarify, or route one bounded capability.");
    if (!ConversationAssistant->StartTurn(
            Request,
            Context,
            PriorHistory))
    {
        AssistantReply = ConversationAssistant->GetStateText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ClearConversation()
{
    if (ConversationAssistant->IsThinking()) return FReply::Handled();
    ConversationAssistant->ResetConversation();
    ConversationMessages.Reset();
    NextConversationMessageNumber = 1;
    LastRequest.Reset();
    LastAssistantAction = ELastAssistantAction::None;
    AssistantReply = TEXT("A new local conversation is ready.");
    if (AssistantPromptBox.IsValid())
        AssistantPromptBox->SetText(FText::GetEmpty());
    AddConversationMessage(
        TEXT("assistant"),
        TEXT("This is a new conversation. Ask about the current Unreal context, request an analysis, or describe a change you want to make."));
    return FReply::Handled();
}

void SRenderMasterWorkspace::HandleConversationTurnCompleted(
    const FRenderMasterConversationTurn& Turn)
{
    AssistantReply = Turn.AssistantMessage;
    AddConversationMessage(TEXT("assistant"), Turn.AssistantMessage);
    if (Turn.Outcome == TEXT("route"))
    {
        DispatchConversationIntent(Turn.Intent, Turn.ActionPrompt);
    }
}

void SRenderMasterWorkspace::HandleConversationTurnFailed(const FString& Error)
{
    AssistantReply = Error;
    AddConversationMessage(TEXT("assistant"), FString::Printf(
        TEXT("I could not complete that local conversation turn: %s"),
        *Error));
}

void SRenderMasterWorkspace::DispatchConversationIntent(
    const FString& Intent,
    const FString& ActionPrompt)
{
    if (!AssistantPromptBox.IsValid()) return;
    AssistantPromptBox->SetText(FText::FromString(ActionPrompt));

    if (Intent == TEXT("material_project")) PrepareAction();
    else if (Intent == TEXT("material_external")) PrepareExternalAction();
    else if (Intent == TEXT("transform")) PrepareTransformAction();
    else if (Intent == TEXT("light")) PrepareLightAction();
    else if (Intent == TEXT("lighting_rig")) PrepareLightingRigAction();
    else if (Intent == TEXT("lighting_rig_review")) ReviewAppliedLightingRigAction();
    else if (Intent == TEXT("camera")) PrepareCameraAction();
    else if (Intent == TEXT("performance_selection")) PreparePerformanceAction();
    else if (Intent == TEXT("level_audit")) PrepareLevelAuditAction();
    else if (Intent == TEXT("runtime_performance"))
        PrepareRuntimePerformanceAction();
    else if (Intent == TEXT("gpu_scope")) PrepareInsightsGpuAction();
    else if (Intent == TEXT("actor_gpu_impact")) PrepareActorGpuImpactAction();
    else if (Intent == TEXT("level_profiling")) PrepareLevelProfilingAction();
    else if (Intent == TEXT("shadow_optimization"))
        PrepareShadowOptimizationAction();
    else if (Intent == TEXT("shadow_verification"))
        PrepareShadowVerificationAction();
    else if (Intent == TEXT("render_evaluate"))
    {
        LastAssistantAction = ELastAssistantAction::None;
        if (Controller->Start(ActionPrompt, 2))
        {
            AssistantReply = TEXT(
                "The transient Render & Evaluate workflow is running. No formal level content is modified by this preview workflow.");
        }
        else
        {
            AssistantReply = Controller->GetDetailText().ToString();
        }
    }
    else
    {
        AssistantReply = FString::Printf(
            TEXT("The conversation router returned an unconnected intent: %s"),
            *Intent);
    }
    AssistantPromptBox->SetText(FText::GetEmpty());
}

bool SRenderMasterWorkspace::CaptureConversationContext(
    FRenderMasterConversationContext& OutContext,
    FString& OutError) const
{
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal Editor context service is unavailable.");
        return false;
    }
    UWorld* World = GEditor->GetEditorWorldContext().World();
    if (World == nullptr || World->GetPackage() == nullptr)
    {
        OutError = TEXT("Open an editable Unreal level before starting a conversation.");
        return false;
    }

    OutContext = FRenderMasterConversationContext();
    OutContext.ProjectName = FApp::GetProjectName();
    OutContext.LevelPath = World->GetPackage()->GetName();
    if (GEditor->PlayWorld != nullptr)
    {
        OutContext.PlayMode = GEditor->bIsSimulatingInEditor
            ? TEXT("sie")
            : TEXT("pie");
    }

    int32 SelectionCount = 0;
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors());
         Iterator;
         ++Iterator)
    {
        AActor* Actor = Cast<AActor>(*Iterator);
        if (Actor == nullptr) continue;
        ++SelectionCount;
        if (OutContext.Selection.Num() >= 32) continue;

        FRenderMasterConversationSelectionItem Item;
        Item.ActorName = Actor->GetActorLabel();
        Item.ActorPath = Actor->GetPathName();
        Item.ActorClass = Actor->GetClass()->GetName();
        if (Actor->IsA<ALight>()) Item.ActorKind = TEXT("light");
        else if (Actor->IsA<ACameraActor>()) Item.ActorKind = TEXT("camera");
        else if (Actor->FindComponentByClass<UStaticMeshComponent>() != nullptr)
            Item.ActorKind = TEXT("static_mesh");
        else Item.ActorKind = TEXT("other");
        OutContext.Selection.Add(MoveTemp(Item));
    }
    OutContext.bSelectionTruncated = SelectionCount > 32;
    OutContext.bLightingRigReviewReady =
        LightingRigReviewAssistant.IsValid()
        && LightingRigReviewAssistant->CanStart();
    if (LevelProfilingAssistant.IsValid())
    {
        const ERenderMasterLevelProfilingState ProfileState =
            LevelProfilingAssistant->GetState();
        OutContext.bShadowOptimizationReady =
            ProfileState == ERenderMasterLevelProfilingState::Complete
            || ProfileState == ERenderMasterLevelProfilingState::Partial;
    }
    OutContext.bShadowVerificationReady =
        ShadowVerificationAssistant.IsValid()
        && ShadowVerificationAssistant->CanStartVerification();
    if (MaterialAssistant->GetState()
        == ERenderMasterMaterialAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("material_project");
    else if (TransformAssistant->GetState()
        == ERenderMasterTransformAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("transform");
    else if (LightAssistant->GetState()
        == ERenderMasterLightAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("light");
    else if (LightingRigAssistant->GetState()
        == ERenderMasterLightingRigAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("lighting_rig");
    else if (LightingRigReviewAssistant->GetState()
        == ERenderMasterLightingRigReviewState::Proposed)
        OutContext.PendingActionIntent = TEXT("lighting_rig_review");
    else if (CameraAssistant->GetState()
        == ERenderMasterCameraAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("camera");
    else if (CameraBatchAssistant->GetState()
        == ERenderMasterCameraBatchAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("camera");
    else if (PerformanceAssistant->GetState()
        == ERenderMasterPerformanceAssistantState::Proposed)
        OutContext.PendingActionIntent = TEXT("performance_selection");
    else if (LevelAuditAssistant->GetState()
        == ERenderMasterLevelAuditState::Proposed)
        OutContext.PendingActionIntent = TEXT("level_audit");
    else if (InsightsGpuAssistant->CanApplyShadowOptimization())
        OutContext.PendingActionIntent = TEXT("shadow_optimization");
    else if (ShadowVerificationAssistant->GetState()
            == ERenderMasterShadowVerificationState::RetainRecommended
        || ShadowVerificationAssistant->GetState()
            == ERenderMasterShadowVerificationState::RollbackRecommended)
        OutContext.PendingActionIntent = TEXT("shadow_verification");
    return true;
}

void SRenderMasterWorkspace::AddConversationMessage(
    const FString& Role,
    const FString& Content)
{
    const FString CleanContent = Content.TrimStartAndEnd();
    if (CleanContent.IsEmpty()) return;
    FRenderMasterConversationMessage Message;
    Message.MessageId = FString::Printf(
        TEXT("message_%06d"),
        NextConversationMessageNumber++);
    Message.Role = Role;
    Message.Content = CleanContent.Left(8000);
    ConversationMessages.Add(MoveTemp(Message));
    if (ConversationMessages.Num() > 100)
        ConversationMessages.RemoveAt(0, ConversationMessages.Num() - 100);
    RefreshConversationMessages();
}

void SRenderMasterWorkspace::RefreshConversationMessages()
{
    if (!ConversationMessagesBox.IsValid()) return;
    ConversationMessagesBox->ClearChildren();
    for (const FRenderMasterConversationMessage& Message : ConversationMessages)
    {
        const bool bUser = Message.Role == TEXT("user");
        ConversationMessagesBox->AddSlot()
        .AutoHeight()
        .Padding(0.0f, 0.0f, 0.0f, 8.0f)
        [
            MakeMessage(
                bUser ? LOCTEXT("ConversationUser", "YOU")
                      : LOCTEXT("ConversationAssistant", "ASSISTANT"),
                FText::FromString(Message.Content),
                bUser ? FLinearColor(0.55f, 0.42f, 0.92f)
                      : FLinearColor(0.16f, 0.58f, 0.95f))
        ];
    }
    if (ConversationScrollBox.IsValid())
        ConversationScrollBox->ScrollToEnd();
}

bool SRenderMasterWorkspace::CanSendConversationMessage() const
{
    if (!ConversationAssistant.IsValid()
        || !ConversationAssistant->CanStart()
        || !AssistantPromptBox.IsValid()
        || AssistantPromptBox->GetText().ToString().TrimStartAndEnd().IsEmpty()
        || !Controller.IsValid()
        || Controller->IsRunning())
    {
        return false;
    }
    const bool bSpecializedToolBusy =
        MaterialAssistant->GetState()
            == ERenderMasterMaterialAssistantState::Searching
        || MaterialAssistant->GetState()
            == ERenderMasterMaterialAssistantState::Importing
        || TransformAssistant->GetState()
            == ERenderMasterTransformAssistantState::Planning
        || LightAssistant->GetState()
            == ERenderMasterLightAssistantState::Planning
        || LightingRigAssistant->GetState()
            == ERenderMasterLightingRigAssistantState::Planning
        || LightingRigReviewAssistant->GetState()
            == ERenderMasterLightingRigReviewState::Capturing
        || LightingRigReviewAssistant->GetState()
            == ERenderMasterLightingRigReviewState::Evaluating
        || CameraAssistant->GetState()
            == ERenderMasterCameraAssistantState::Planning
        || CameraBatchAssistant->GetState()
            == ERenderMasterCameraBatchAssistantState::Planning
        || PerformanceAssistant->GetState()
            == ERenderMasterPerformanceAssistantState::Planning
        || LevelAuditAssistant->IsBusy()
        || RuntimePerformanceAssistant->IsBusy()
        || InsightsGpuAssistant->IsBusy()
        || LevelProfilingAssistant->IsBusy()
        || ShadowVerificationAssistant->GetState()
            == ERenderMasterShadowVerificationState::Profiling
        || ShadowVerificationAssistant->GetState()
            == ERenderMasterShadowVerificationState::Analyzing;
    return !bSpecializedToolBusy;
}

FReply SRenderMasterWorkspace::AnalyzeContext()
{
    LastRequest = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (LastRequest.IsEmpty()) LastRequest = TEXT("Analyze the current Unreal context.");
    AssistantReply = TEXT("I captured the live project, level, and actor selection below. No project content was changed. Single-slot meshes are targeted automatically; for a multi-slot mesh, choose the exact material slot before preparing an action.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a request before preparing an action. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Material;
    FString SelectionError;
    UStaticMeshComponent* Component = GetSingleSelectedStaticMeshComponent(SelectionError);
    if (Component == nullptr)
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    int32 TargetSlotIndex = INDEX_NONE;
    if (!ResolveTargetMaterialSlot(Component, TargetSlotIndex, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (MaterialAssistant->StartProposal(Prompt, Component, TargetSlotIndex))
    {
        AssistantReply = TEXT("I am searching the validated project catalog for a material that matches your request. No Editor scene change has been applied.");
    }
    else
    {
        AssistantReply = MaterialAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareExternalAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a material request before searching Poly Haven. Nothing has been downloaded or changed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Material;
    FString SelectionError;
    UStaticMeshComponent* Component = GetSingleSelectedStaticMeshComponent(SelectionError);
    if (Component == nullptr)
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    int32 TargetSlotIndex = INDEX_NONE;
    if (!ResolveTargetMaterialSlot(Component, TargetSlotIndex, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (MaterialAssistant->StartExternalProposal(Prompt, Component, TargetSlotIndex))
    {
        AssistantReply = TEXT("I am searching Poly Haven and verifying the top CC0 material in the workstation cache. Unreal Content will not change until you review and approve the exact import proposal.");
    }
    else
    {
        AssistantReply = MaterialAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareTransformAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a world- or local-space Transform request before preparing an action. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Transform;
    FString SelectionError;
    TArray<AActor*> Actors;
    if (!GetSelectedActorsForTransform(Actors, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (TransformAssistant->StartProposal(Prompt, Actors))
    {
        AssistantReply = FString::Printf(
            TEXT("I froze %d selected Actor identities and their current Transforms, then started preparing one bounded world/local-space proposal. No scene change has been applied."),
            Actors.Num());
    }
    else
    {
        AssistantReply = TransformAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareLightAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a light property request before preparing an action. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Light;
    FString SelectionError;
    TArray<ALight*> LightActors;
    if (!GetSelectedLights(LightActors, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (LightAssistant->StartProposal(Prompt, LightActors))
    {
        AssistantReply = FString::Printf(
            TEXT("I froze %d selected light identities, types, units, components, and current properties, then started preparing one compatible group proposal. No scene change has been applied."),
            LightActors.Num());
    }
    else
    {
        AssistantReply = LightAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareLightingRigAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a three-point-lighting request before preparing a rig. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::LightingRig;
    LightingRigReviewAssistant->Reset();
    FString SelectionError;
    AActor* SubjectActor = nullptr;
    ACameraActor* CameraActor = nullptr;
    TArray<ALight*> LightActors;
    if (!GetSelectedLightingRig(
            SubjectActor, CameraActor, LightActors, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (LightingRigAssistant->StartProposal(
            Prompt, SubjectActor, CameraActor, LightActors))
    {
        AssistantReply = TEXT("I froze the subject identity and bounds, camera viewpoint, and three local-light identities, locations, units, and properties. I am preparing a bounded Key/Fill/Rim proposal; no scene change has been applied.");
    }
    else
    {
        AssistantReply = LightingRigAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ReviewAppliedLightingRigAction()
{
    LastAssistantAction = ELastAssistantAction::LightingRigReview;
    if (LightingRigReviewAssistant->StartReview())
    {
        AssistantReply = TEXT("I am capturing one Lit PNG from the frozen rig camera, restoring your active perspective viewport, and asking the local vision model to assess exposure, Fill balance, and Rim separation. No level change has been applied.");
    }
    else
    {
        AssistantReply = LightingRigReviewAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareCameraAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a numeric camera Transform, lens, focus, or exposure-compensation request before preparing an action. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    FString SelectionError;
    TArray<ACameraActor*> CameraActors;
    if (!GetSelectedCameras(CameraActors, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (CameraActors.Num() == 1)
    {
        LastAssistantAction = ELastAssistantAction::Camera;
        if (CameraAssistant->StartProposal(Prompt, CameraActors[0]))
        {
            AssistantReply = TEXT("I froze the selected camera identity, type, lens limits, Transform, focus, and exposure compensation, then started preparing a bounded proposal. No scene change has been applied.");
        }
        else
        {
            AssistantReply = CameraAssistant->GetSummaryText().ToString();
        }
    }
    else
    {
        LastAssistantAction = ELastAssistantAction::CameraBatch;
        if (CameraBatchAssistant->StartProposal(Prompt, CameraActors))
        {
            AssistantReply = FString::Printf(
                TEXT("I froze %d selected camera identities, types, lens bounds, Transforms, focus, and exposure values, then started preparing one coordinated proposal. No scene change has been applied."),
                CameraActors.Num());
        }
        else
        {
            AssistantReply = CameraBatchAssistant->GetSummaryText().ToString();
        }
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveAction()
{
    if (MaterialAssistant->ApplyProposal())
    {
        AssistantReply = MaterialAssistant->GetState() == ERenderMasterMaterialAssistantState::Importing
            ? TEXT("The exact external proposal was approved. I am creating five Unreal assets, synchronizing the catalog and Chroma, then I will apply the new material to the captured slot.")
            : TEXT("The approved material override was applied to the captured component. The level was not saved automatically. Use Ctrl+Z to undo it.");
    }
    else
    {
        AssistantReply = MaterialAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectAction()
{
    MaterialAssistant->RejectProposal();
    AssistantReply = TEXT("The proposed material action was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveTransformAction()
{
    LastAssistantAction = ELastAssistantAction::Transform;
    if (TransformAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved Transform was applied to the complete captured selection as one grouped Editor action. The level was not saved automatically. Use Ctrl+Z once to undo the batch.");
    }
    else
    {
        AssistantReply = TransformAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectTransformAction()
{
    LastAssistantAction = ELastAssistantAction::Transform;
    TransformAssistant->RejectProposal();
    AssistantReply = TEXT("The proposed Transform action was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveLightAction()
{
    LastAssistantAction = ELastAssistantAction::Light;
    if (LightAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved light properties were applied to the complete captured selection as one grouped Editor action. The level was not saved automatically. Use Ctrl+Z once to undo the group.");
    }
    else
    {
        AssistantReply = LightAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectLightAction()
{
    LastAssistantAction = ELastAssistantAction::Light;
    LightAssistant->RejectProposal();
    AssistantReply = TEXT("The proposed light action was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveLightingRigAction()
{
    LastAssistantAction = ELastAssistantAction::LightingRig;
    if (LightingRigAssistant->ApplyProposal())
    {
        LightingRigReviewAssistant->Reset();
        AssistantReply = TEXT("The approved Key/Fill/Rim rig was applied to all three captured lights as one grouped Editor action. The subject and camera were not changed, and the level was not saved automatically. Use Ctrl+Z once to undo the rig.");
    }
    else
    {
        AssistantReply = LightingRigAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectLightingRigAction()
{
    LastAssistantAction = ELastAssistantAction::LightingRig;
    LightingRigAssistant->RejectProposal();
    AssistantReply = TEXT("The proposed lighting rig was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveLightingRigReviewAction()
{
    LastAssistantAction = ELastAssistantAction::LightingRigReview;
    if (LightingRigReviewAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved intensity-only visual correction was applied to the three rig lights as one grouped Editor action. The level was not saved. One Ctrl+Z restores the pre-correction rig; a second Ctrl+Z restores the original lights.");
    }
    else
    {
        AssistantReply = LightingRigReviewAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectLightingRigReviewAction()
{
    LastAssistantAction = ELastAssistantAction::LightingRigReview;
    LightingRigReviewAssistant->RejectProposal();
    AssistantReply = TEXT("The visual intensity correction was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveCameraAction()
{
    LastAssistantAction = ELastAssistantAction::Camera;
    if (CameraAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved camera properties were applied to the captured camera. The level was not saved automatically. Use Ctrl+Z to undo them.");
    }
    else
    {
        AssistantReply = CameraAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectCameraAction()
{
    LastAssistantAction = ELastAssistantAction::Camera;
    CameraAssistant->RejectProposal();
    AssistantReply = TEXT("The proposed camera action was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveCameraBatchAction()
{
    LastAssistantAction = ELastAssistantAction::CameraBatch;
    if (CameraBatchAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved coordinated camera properties were applied to the complete captured selection as one grouped Editor action. The level was not saved automatically. Use Ctrl+Z once to undo the batch.");
    }
    else
    {
        AssistantReply = CameraBatchAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectCameraBatchAction()
{
    LastAssistantAction = ELastAssistantAction::CameraBatch;
    CameraBatchAssistant->RejectProposal();
    AssistantReply = TEXT("The coordinated camera proposal was rejected. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareLevelAuditAction()
{
    const FString Prompt = AssistantPromptBox.IsValid()
        ? AssistantPromptBox->GetText().ToString().TrimStartAndEnd()
        : FString();
    LastRequest = Prompt.IsEmpty()
        ? TEXT("Audit this complete level and prioritize evidence-backed optimization work.")
        : Prompt;
    LastAssistantAction = ELastAssistantAction::LevelAudit;
    if (LevelAuditAssistant->StartAudit(Prompt))
    {
        AssistantReply = TEXT("I am capturing every eligible native StaticMeshActor in the current level, computing deterministic static priorities, and preparing a constrained review. Static scores are not runtime measurements. No scene change has been applied.");
    }
    else
    {
        AssistantReply = LevelAuditAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveLevelAuditAction()
{
    LastAssistantAction = ELastAssistantAction::LevelAudit;
    if (LevelAuditAssistant->ApplyActions())
    {
        AssistantReply = TEXT("The approved full-level audit actions were revalidated and applied as one grouped Editor transaction. The level was not saved automatically. Use Ctrl+Z once to undo the complete batch.");
    }
    else
    {
        AssistantReply = LevelAuditAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectLevelAuditAction()
{
    LastAssistantAction = ELastAssistantAction::LevelAudit;
    LevelAuditAssistant->Reject();
    AssistantReply = TEXT("The full-level audit or its proposed settings were dismissed. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PreparePerformanceAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a performance review or bounded component-setting request before preparing the review. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Performance;
    FString SelectionError;
    TArray<AStaticMeshActor*> Actors;
    if (!GetSelectedStaticMeshActors(Actors, SelectionError))
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (PerformanceAssistant->StartProposal(Prompt, Actors))
    {
        AssistantReply = FString::Printf(
            TEXT("I froze measured mesh, LOD, material, Nanite, collision, Tick, shadow, culling, and bounds evidence for %d selected StaticMeshActors. I am preparing an evidence-backed review; no scene change has been applied."),
            Actors.Num());
    }
    else
    {
        AssistantReply = PerformanceAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApprovePerformanceAction()
{
    LastAssistantAction = ELastAssistantAction::Performance;
    if (PerformanceAssistant->ApplyProposal())
    {
        AssistantReply = TEXT("The approved Cast Shadow and Max Draw Distance settings were applied to the complete captured selection as one grouped Editor action. The level was not saved automatically. Use Ctrl+Z once to undo the batch.");
    }
    else
    {
        AssistantReply = PerformanceAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectPerformanceAction()
{
    LastAssistantAction = ELastAssistantAction::Performance;
    PerformanceAssistant->RejectProposal();
    AssistantReply = TEXT("The performance review or proposed settings were dismissed. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareRuntimePerformanceAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a runtime performance question before starting the PIE/SIE capture. Nothing has been changed.");
        return FReply::Handled();
    }
    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::RuntimePerformance;
    if (RuntimePerformanceAssistant->StartReview(Prompt))
    {
        AssistantReply = TEXT("I am warming up the active PIE/SIE viewport, then capturing 120 consecutive frame, thread, GPU, process-memory, and RHI texture-memory samples. Keep the camera and workload representative. This review is read-only.");
    }
    else
    {
        AssistantReply = RuntimePerformanceAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::DismissRuntimePerformanceAction()
{
    LastAssistantAction = ELastAssistantAction::RuntimePerformance;
    RuntimePerformanceAssistant->Dismiss();
    AssistantReply = TEXT("The runtime performance capture or review was dismissed. No Editor scene change was applied.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareInsightsGpuAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a GPU scope performance question before starting the Unreal Insights trace. Nothing has been changed.");
        return FReply::Handled();
    }
    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::InsightsGpu;
    if (InsightsGpuAssistant->StartReview(Prompt))
    {
        AssistantReply = TEXT("I am recording five seconds of CPU, GPU, frame, and bookmark trace channels from the active PIE/SIE workload. Unreal will parse the resulting GPU timelines before the local model sees any evidence. No scene property is being changed.");
    }
    else
    {
        AssistantReply = InsightsGpuAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareActorGpuImpactAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a selected-Actor GPU impact question before starting the A/B experiment. Nothing has been changed.");
        return FReply::Handled();
    }
    FString SelectionError;
    AActor* SelectedActor = GetSingleSelectedActor(SelectionError);
    if (SelectedActor == nullptr)
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::InsightsGpu;
    if (InsightsGpuAssistant->StartActorImpactStudy(Prompt, SelectedActor))
    {
        AssistantReply = TEXT("I am running two visible A/A controls followed by three counterbalanced AB/BA/AB visible-hidden pairs. Each of the eight traces has a fixed warmup. Keep the camera and workload unchanged for about one minute; the runtime Actor will be restored before review and on any failure. The formal level is never edited or saved.");
    }
    else
    {
        AssistantReply = InsightsGpuAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareShadowOptimizationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowOptimization;
    if (!LevelProfilingAssistant.IsValid() || !LevelAuditAssistant.IsValid())
    {
        AssistantReply = TEXT("The profiling evidence services are unavailable. Nothing has changed.");
        return FReply::Handled();
    }
    const ERenderMasterLevelProfilingState ProfileState =
        LevelProfilingAssistant->GetState();
    if (ProfileState != ERenderMasterLevelProfilingState::Complete
        && ProfileState != ERenderMasterLevelProfilingState::Partial)
    {
        AssistantReply = TEXT("Run Profiling-First Level Audit first. Shadow optimization requires a measured stable Actor candidate from the current level and workload.");
        return FReply::Handled();
    }
    AStaticMeshActor* Candidate = nullptr;
    FString CandidateName;
    for (const FRenderMasterLevelProfilingActorFinding& Finding
        : LevelProfilingAssistant->GetReport().ActorFindings)
    {
        if (Finding.Outcome != TEXT("stable_candidate")) continue;
        AStaticMeshActor* Actor = LevelAuditAssistant->FindCapturedActorByPath(
            Finding.ActorPath);
        UStaticMeshComponent* Component = IsValid(Actor)
            ? Actor->GetStaticMeshComponent()
            : nullptr;
        if (IsValid(Component) && Component->CastShadow)
        {
            Candidate = Actor;
            CandidateName = Finding.ActorName;
            break;
        }
    }
    if (!IsValid(Candidate))
    {
        AssistantReply = TEXT("The latest profiling report has no stable candidate whose formal Static Mesh Component still has Cast Shadow enabled. No experiment was started.");
        return FReply::Handled();
    }
    const FString Prompt = AssistantPromptBox.IsValid()
        ? AssistantPromptBox->GetText().ToString().TrimStartAndEnd()
        : FString();
    LastRequest = Prompt.IsEmpty()
        ? FString::Printf(
            TEXT("Test whether disabling Cast Shadow for %s produces a repeatable GPU improvement."),
            *CandidateName)
        : Prompt;
    if (InsightsGpuAssistant->StartActorShadowOptimizationStudy(
            LastRequest, Candidate))
    {
        AssistantReply = FString::Printf(
            TEXT("I selected %s from the latest stable profiling candidates. I am running two Cast-Shadow-on A/A controls and three counterbalanced on/off pairs. Each of the eight GPU traces has a fixed warmup. Keep the PIE/SIE camera and workload unchanged; runtime Cast Shadow will be restored before any decision."),
            *CandidateName);
    }
    else
    {
        AssistantReply = InsightsGpuAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::ApproveShadowOptimizationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowOptimization;
    if (InsightsGpuAssistant->ApplyShadowOptimizationProposal())
    {
        const FRenderMasterShadowOptimizationReport& ShadowReport =
            InsightsGpuAssistant->GetShadowOptimizationReport();
        AStaticMeshActor* Actor = LevelAuditAssistant.IsValid()
            ? LevelAuditAssistant->FindCapturedActorByPath(
                ShadowReport.ProposedAction.Target.ActorPath)
            : nullptr;
        UStaticMeshComponent* Component = IsValid(Actor)
            ? Actor->GetStaticMeshComponent()
            : nullptr;
        const bool bVerificationArmed =
            ShadowVerificationAssistant.IsValid()
            && LevelProfilingAssistant.IsValid()
            && ShadowVerificationAssistant->ArmAfterApply(
                Actor,
                Component,
                ShadowReport.ProposedAction,
                LevelProfilingAssistant->GetReportOutputPath(),
                LevelProfilingAssistant->GetReport(),
                InsightsGpuAssistant->GetReportOutputPath(),
                ShadowReport);
        AssistantReply = bVerificationArmed
            ? TEXT("The measured Cast Shadow change was applied to the exact formal Actor as one unsaved Unreal Undo transaction. Before evidence is frozen. Restart the same representative PIE/SIE workload, then use Run Post-Apply Verification.")
            : TEXT("The measured Cast Shadow change was applied and remains unsaved, but post-apply verification could not be armed safely. Use Ctrl+Z to restore the original shadow setting, then rerun the complete profiling and optimization chain.");
    }
    else
    {
        AssistantReply = InsightsGpuAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareShadowVerificationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowVerification;
    const FString Prompt = AssistantPromptBox.IsValid()
        ? AssistantPromptBox->GetText().ToString().TrimStartAndEnd()
        : FString();
    LastRequest = Prompt.IsEmpty()
        ? TEXT("Repeat the complete fixed profiling workload after the approved Cast Shadow optimization.")
        : Prompt;
    if (ShadowVerificationAssistant->StartVerification(Prompt))
    {
        AssistantReply = TEXT(
            "I started fresh whole-level post-apply verification. Keep the PIE/SIE camera, viewport, resolution, content state, and workload unchanged while the complete profiling chain repeats. No additional formal setting will be edited or saved.");
    }
    else
    {
        AssistantReply = ShadowVerificationAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::KeepShadowOptimizationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowVerification;
    if (ShadowVerificationAssistant->KeepAppliedOptimization())
    {
        AssistantReply = ShadowVerificationAssistant->GetSummaryText().ToString();
    }
    else
    {
        AssistantReply = TEXT(
            "The current formal target no longer matches the verified applied state. The keep decision was not recorded.");
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RollbackShadowOptimizationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowVerification;
    if (ShadowVerificationAssistant->RollbackAppliedOptimization())
    {
        AssistantReply = TEXT(
            "The exact Cast Shadow setting was restored as one new Unreal transaction. The level was not saved. One Ctrl+Z reapplies the optimized false state.");
    }
    else
    {
        AssistantReply = ShadowVerificationAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::DismissShadowVerificationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowVerification;
    const bool bWasBusy = ShadowVerificationAssistant->IsBusy();
    ShadowVerificationAssistant->Dismiss();
    AssistantReply = bWasBusy
        ? TEXT("Post-apply verification was cancelled. Temporary runtime state was restored; the unsaved formal shadow change remains until you keep it, roll it back, or undo it manually.")
        : TEXT("Post-apply verification was dismissed. Evidence artifacts remain local and the level was not saved.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::RejectShadowOptimizationAction()
{
    LastAssistantAction = ELastAssistantAction::ShadowOptimization;
    InsightsGpuAssistant->RejectShadowOptimizationProposal();
    AssistantReply = TEXT("The measured shadow proposal was rejected. Runtime state was already restored and no formal level property was changed.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::PrepareLevelProfilingAction()
{
    const FString Prompt = AssistantPromptBox.IsValid()
        ? AssistantPromptBox->GetText().ToString().TrimStartAndEnd()
        : FString();
    LastRequest = Prompt.IsEmpty()
        ? TEXT("Profile this complete level and identify measured rendering bottlenecks.")
        : Prompt;
    LastAssistantAction = ELastAssistantAction::LevelProfiling;
    if (LevelProfilingAssistant->Start(Prompt))
    {
        AssistantReply = TEXT(
            "I started one profiling-first audit against the active fixed PIE/SIE workload. I will run the static funnel, 120-frame baseline, five-second GPU trace, and up to two repeatable Actor studies. Keep the camera, resolution, and workload stable. No production level property will be changed or saved.");
    }
    else
    {
        AssistantReply = LevelProfilingAssistant->GetSummaryText().ToString();
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::DismissLevelProfilingAction()
{
    LastAssistantAction = ELastAssistantAction::LevelProfiling;
    const bool bWasBusy = LevelProfilingAssistant->IsBusy();
    LevelProfilingAssistant->Cancel();
    AssistantReply = bWasBusy
        ? TEXT("The profiling audit was cancelled. Temporary runtime state was restored, and no production level content was saved.")
        : TEXT("The profiling report was dismissed. Its local evidence artifacts remain in the workflow folder.");
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::OpenInsightsGpuTraceAction()
{
    LastAssistantAction = ELastAssistantAction::InsightsGpu;
    FString Error;
    if (InsightsGpuAssistant->OpenTraceInInsights(Error))
    {
        AssistantReply = InsightsGpuAssistant->IsShadowOptimizationStudy()
            ? TEXT("The final Cast-Shadow-off trace is opening in Unreal Insights. All eight authoritative traces remain in the same workflow folder.")
            : InsightsGpuAssistant->IsRepeatableActorImpactStudy()
            ? TEXT("The final Actor-hidden study trace is opening in Unreal Insights. All eight authoritative traces remain in the same workflow folder.")
            : TEXT("The preserved trace is opening in Unreal Insights. Use its GPU tracks to inspect the same raw timeline behind the Assistant review.");
    }
    else
    {
        AssistantReply = Error;
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::OpenInsightsGpuBaselineTraceAction()
{
    LastAssistantAction = ELastAssistantAction::InsightsGpu;
    FString Error;
    if (InsightsGpuAssistant->OpenBaselineTraceInInsights(Error))
    {
        AssistantReply = InsightsGpuAssistant->IsShadowOptimizationStudy()
            ? TEXT("The first Cast-Shadow-on A/A control trace is opening in Unreal Insights. The decision uses only scopes present in all eight Top-64 sets.")
            : InsightsGpuAssistant->IsRepeatableActorImpactStudy()
            ? TEXT("The first Actor-visible A/A control trace is opening in Unreal Insights. The study aggregates only scopes present in all eight Top-64 sets.")
            : TEXT("The Actor-visible baseline is opening in Unreal Insights. Compare it with the preserved Actor-hidden trace; the Assistant report uses only scopes present in both Top-64 sets.");
    }
    else
    {
        AssistantReply = Error;
    }
    return FReply::Handled();
}

FReply SRenderMasterWorkspace::DismissInsightsGpuAction()
{
    LastAssistantAction = InsightsGpuAssistant->IsShadowOptimizationStudy()
        ? ELastAssistantAction::ShadowOptimization
        : ELastAssistantAction::InsightsGpu;
    InsightsGpuAssistant->Dismiss();
    AssistantReply = InsightsGpuAssistant->IsShadowOptimizationStudy()
        ? TEXT("The shadow optimization experiment was dismissed. Runtime state is restored, no formal change was applied, and completed traces remain in the workflow folder.")
        : TEXT("The GPU scope review was dismissed. No Editor scene change was applied, and the .utrace artifact remains in the workflow folder.");
    return FReply::Handled();
}

void SRenderMasterWorkspace::SetPage(int32 PageIndex)
{
    ActivePage = FMath::Clamp(PageIndex, 0, 1);
    if (PageSwitcher.IsValid()) PageSwitcher->SetActiveWidgetIndex(ActivePage);
}

bool SRenderMasterWorkspace::CanPrepareAssistantAction() const
{
    if (!Controller.IsValid()
        || !ConversationAssistant.IsValid()
        || !MaterialAssistant.IsValid()
        || !TransformAssistant.IsValid()
        || !LightAssistant.IsValid()
        || !LightingRigAssistant.IsValid()
        || !LightingRigReviewAssistant.IsValid()
        || !CameraAssistant.IsValid()
        || !CameraBatchAssistant.IsValid()
        || !PerformanceAssistant.IsValid()
        || !LevelAuditAssistant.IsValid()
        || !RuntimePerformanceAssistant.IsValid()
        || !InsightsGpuAssistant.IsValid()
        || !LevelProfilingAssistant.IsValid()
        || !ShadowVerificationAssistant.IsValid())
    {
        return false;
    }
    const ERenderMasterMaterialAssistantState MaterialState = MaterialAssistant->GetState();
    const ERenderMasterTransformAssistantState TransformState = TransformAssistant->GetState();
    const ERenderMasterLightAssistantState LightState = LightAssistant->GetState();
    const ERenderMasterLightingRigAssistantState RigState = LightingRigAssistant->GetState();
    const ERenderMasterLightingRigReviewState RigReviewState =
        LightingRigReviewAssistant->GetState();
    const ERenderMasterCameraAssistantState CameraState = CameraAssistant->GetState();
    const ERenderMasterCameraBatchAssistantState CameraBatchState =
        CameraBatchAssistant->GetState();
    const ERenderMasterPerformanceAssistantState PerformanceState =
        PerformanceAssistant->GetState();
    const bool bLevelAuditPending = LevelAuditAssistant->IsBusy()
        || LevelAuditAssistant->GetState() == ERenderMasterLevelAuditState::Proposed;
    const bool bRuntimePerformancePending = RuntimePerformanceAssistant->IsBusy();
    const bool bInsightsGpuPending = InsightsGpuAssistant->IsBusy();
    const bool bLevelProfilingPending = LevelProfilingAssistant->IsBusy();
    const ERenderMasterShadowVerificationState ShadowVerificationState =
        ShadowVerificationAssistant->GetState();
    const bool bShadowVerificationPending =
        ShadowVerificationState != ERenderMasterShadowVerificationState::Ready
        && ShadowVerificationState != ERenderMasterShadowVerificationState::Dismissed
        && ShadowVerificationState != ERenderMasterShadowVerificationState::Retained
        && ShadowVerificationState != ERenderMasterShadowVerificationState::RolledBack;
    const bool bMaterialPending = MaterialState == ERenderMasterMaterialAssistantState::Searching
        || MaterialState == ERenderMasterMaterialAssistantState::Importing
        || MaterialState == ERenderMasterMaterialAssistantState::Proposed;
    const bool bTransformPending = TransformState == ERenderMasterTransformAssistantState::Planning
        || TransformState == ERenderMasterTransformAssistantState::Proposed;
    const bool bLightPending = LightState == ERenderMasterLightAssistantState::Planning
        || LightState == ERenderMasterLightAssistantState::Proposed;
    const bool bRigPending = RigState == ERenderMasterLightingRigAssistantState::Planning
        || RigState == ERenderMasterLightingRigAssistantState::Proposed;
    const bool bRigReviewPending =
        RigReviewState == ERenderMasterLightingRigReviewState::Capturing
        || RigReviewState == ERenderMasterLightingRigReviewState::Evaluating
        || RigReviewState == ERenderMasterLightingRigReviewState::Proposed;
    const bool bCameraPending = CameraState == ERenderMasterCameraAssistantState::Planning
        || CameraState == ERenderMasterCameraAssistantState::Proposed;
    const bool bCameraBatchPending =
        CameraBatchState == ERenderMasterCameraBatchAssistantState::Planning
        || CameraBatchState == ERenderMasterCameraBatchAssistantState::Proposed;
    const bool bPerformancePending =
        PerformanceState == ERenderMasterPerformanceAssistantState::Planning
        || PerformanceState == ERenderMasterPerformanceAssistantState::Proposed;
    return Controller->CanStart()
        && ConversationAssistant->CanStart()
        && MaterialAssistant->CanStart()
        && TransformAssistant->CanStart()
        && LightAssistant->CanStart()
        && LightingRigAssistant->CanStart()
        && CameraAssistant->CanStart()
        && CameraBatchAssistant->CanStart()
        && PerformanceAssistant->CanStart()
        && LevelAuditAssistant->CanStart()
        && RuntimePerformanceAssistant->CanStart()
        && InsightsGpuAssistant->CanStart()
        && LevelProfilingAssistant->CanStart()
        && !bMaterialPending
        && !bTransformPending
        && !bLightPending
        && !bRigPending
        && !bRigReviewPending
        && !bCameraPending
        && !bCameraBatchPending
        && !bPerformancePending
        && !bLevelAuditPending
        && !bRuntimePerformancePending
        && !bInsightsGpuPending
        && !bLevelProfilingPending
        && !bShadowVerificationPending;
}

FText SRenderMasterWorkspace::GetProjectContext() const
{
    FString LevelName = TEXT("No editor world");
    if (GEditor != nullptr)
    {
        if (const UWorld* World = GEditor->GetEditorWorldContext(false).World())
        {
            LevelName = World->GetMapName();
        }
    }
    return FText::FromString(FString::Printf(TEXT("Project\n%s\n\nCurrent level\n%s"), FApp::GetProjectName(), *LevelName));
}

FText SRenderMasterWorkspace::GetSelectionContext() const
{
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        return LOCTEXT("NoSelectionService", "Selected actors\nSelection service unavailable");
    }

    TArray<FString> ActorLabels;
    int32 TotalActors = 0;
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        if (const AActor* Actor = Cast<AActor>(*Iterator))
        {
            ++TotalActors;
            if (ActorLabels.Num() < 6)
            {
                ActorLabels.Add(FString::Printf(TEXT("• %s  [%s]"), *Actor->GetActorLabel(), *Actor->GetClass()->GetName()));
            }
        }
    }

    FString Result = FString::Printf(TEXT("Selected actors\n%d"), TotalActors);
    if (!ActorLabels.IsEmpty()) Result += TEXT("\n\n") + FString::Join(ActorLabels, TEXT("\n"));
    if (TotalActors > ActorLabels.Num()) Result += FString::Printf(TEXT("\n• +%d more"), TotalActors - ActorLabels.Num());
    if (TotalActors == 0) Result += TEXT("\n\nSelect actors in the viewport to give the assistant a narrower scope.");
    return FText::FromString(Result);
}

FText SRenderMasterWorkspace::GetAssistantReply() const
{
    if (ConversationAssistant.IsValid() && ConversationAssistant->IsThinking())
        return ConversationAssistant->GetStateText();
    if (LastAssistantAction == ELastAssistantAction::ShadowVerification
        && ShadowVerificationAssistant.IsValid())
    {
        const ERenderMasterShadowVerificationState State =
            ShadowVerificationAssistant->GetState();
        if (State != ERenderMasterShadowVerificationState::Ready
            && State != ERenderMasterShadowVerificationState::Dismissed)
        {
            return ShadowVerificationAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::LevelProfiling
        && LevelProfilingAssistant.IsValid())
    {
        const ERenderMasterLevelProfilingState State =
            LevelProfilingAssistant->GetState();
        if (LevelProfilingAssistant->IsBusy()
            || State == ERenderMasterLevelProfilingState::Complete
            || State == ERenderMasterLevelProfilingState::Partial
            || State == ERenderMasterLevelProfilingState::Failed
            || State == ERenderMasterLevelProfilingState::Cancelled)
        {
            return LevelProfilingAssistant->GetSummaryText();
        }
    }
    if ((LastAssistantAction == ELastAssistantAction::InsightsGpu
            || LastAssistantAction == ELastAssistantAction::ShadowOptimization)
        && InsightsGpuAssistant.IsValid())
    {
        const ERenderMasterInsightsGpuState State = InsightsGpuAssistant->GetState();
        if (InsightsGpuAssistant->IsBusy()
            || State == ERenderMasterInsightsGpuState::Complete
            || State == ERenderMasterInsightsGpuState::Unresolved
            || State == ERenderMasterInsightsGpuState::Failed)
        {
            return InsightsGpuAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::RuntimePerformance
        && RuntimePerformanceAssistant.IsValid())
    {
        const ERenderMasterRuntimePerformanceState State =
            RuntimePerformanceAssistant->GetState();
        if (RuntimePerformanceAssistant->IsBusy()
            || State == ERenderMasterRuntimePerformanceState::Complete
            || State == ERenderMasterRuntimePerformanceState::Unresolved
            || State == ERenderMasterRuntimePerformanceState::Failed)
        {
            return RuntimePerformanceAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::LevelAudit
        && LevelAuditAssistant.IsValid())
    {
        const ERenderMasterLevelAuditState State = LevelAuditAssistant->GetState();
        if (LevelAuditAssistant->IsBusy()
            || State == ERenderMasterLevelAuditState::Proposed
            || State == ERenderMasterLevelAuditState::ReviewOnly
            || State == ERenderMasterLevelAuditState::Unresolved
            || State == ERenderMasterLevelAuditState::Failed)
        {
            return LevelAuditAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::Performance
        && PerformanceAssistant.IsValid())
    {
        const ERenderMasterPerformanceAssistantState State =
            PerformanceAssistant->GetState();
        if (PerformanceAssistant->IsPlanning()
            || State == ERenderMasterPerformanceAssistantState::Proposed
            || State == ERenderMasterPerformanceAssistantState::ReviewOnly
            || State == ERenderMasterPerformanceAssistantState::Unresolved
            || State == ERenderMasterPerformanceAssistantState::Failed)
        {
            return PerformanceAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::LightingRigReview
        && LightingRigReviewAssistant.IsValid())
    {
        const ERenderMasterLightingRigReviewState State =
            LightingRigReviewAssistant->GetState();
        if (LightingRigReviewAssistant->IsBusy()
            || State == ERenderMasterLightingRigReviewState::Proposed
            || State == ERenderMasterLightingRigReviewState::Passed
            || State == ERenderMasterLightingRigReviewState::Unresolved
            || State == ERenderMasterLightingRigReviewState::Failed)
        {
            return LightingRigReviewAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::LightingRig
        && LightingRigAssistant.IsValid())
    {
        if (LightingRigAssistant->IsPlanning())
        {
            return LightingRigAssistant->GetSummaryText();
        }
        if (LightingRigAssistant->GetState()
            == ERenderMasterLightingRigAssistantState::Proposed)
        {
            return LOCTEXT("LightingRigProposedReply", "I prepared one subject- and camera-aware Key/Fill/Rim action. Review every role, selected light, location, intensity, unit, and complete Before/After evidence before approving.");
        }
        if (LightingRigAssistant->GetState()
                == ERenderMasterLightingRigAssistantState::Unresolved
            || LightingRigAssistant->GetState()
                == ERenderMasterLightingRigAssistantState::Failed)
        {
            return LightingRigAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::Camera && CameraAssistant.IsValid())
    {
        if (CameraAssistant->IsPlanning())
        {
            return CameraAssistant->GetSummaryText();
        }
        if (CameraAssistant->GetState() == ERenderMasterCameraAssistantState::Proposed)
        {
            return LOCTEXT("CameraProposedReply", "I prepared a type-specific camera change. Review the exact Camera or Cine Camera, frozen lens limits, changed properties, and Before/After values before approving.");
        }
        if (CameraAssistant->GetState() == ERenderMasterCameraAssistantState::Unresolved
            || CameraAssistant->GetState() == ERenderMasterCameraAssistantState::Failed)
        {
            return CameraAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::CameraBatch
        && CameraBatchAssistant.IsValid())
    {
        if (CameraBatchAssistant->IsPlanning())
        {
            return CameraBatchAssistant->GetSummaryText();
        }
        if (CameraBatchAssistant->GetState()
            == ERenderMasterCameraBatchAssistantState::Proposed)
        {
            return LOCTEXT(
                "CameraBatchProposedReply",
                "I prepared one coordinated camera action for the complete frozen selection. Review every camera, lens bound, changed property, and Before/After value before approving.");
        }
        if (CameraBatchAssistant->GetState()
                == ERenderMasterCameraBatchAssistantState::Unresolved
            || CameraBatchAssistant->GetState()
                == ERenderMasterCameraBatchAssistantState::Failed)
        {
            return CameraBatchAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::Light && LightAssistant.IsValid())
    {
        if (LightAssistant->IsPlanning())
        {
            return LightAssistant->GetSummaryText();
        }
        if (LightAssistant->GetState() == ERenderMasterLightAssistantState::Proposed)
        {
            return LOCTEXT("LightProposedReply", "I prepared one compatible light-group property action. Review every selected light, frozen unit, changed property, and Before/After value before approving.");
        }
        if (LightAssistant->GetState() == ERenderMasterLightAssistantState::Unresolved
            || LightAssistant->GetState() == ERenderMasterLightAssistantState::Failed)
        {
            return LightAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::Transform && TransformAssistant.IsValid())
    {
        if (TransformAssistant->IsPlanning())
        {
            return TransformAssistant->GetSummaryText();
        }
        if (TransformAssistant->GetState() == ERenderMasterTransformAssistantState::Proposed)
        {
            return LOCTEXT("TransformProposedReply", "I prepared one bounded world/local-space Transform action for the complete frozen selection. Review every Actor and its Before/After values before approving.");
        }
        if (TransformAssistant->GetState() == ERenderMasterTransformAssistantState::Unresolved
            || TransformAssistant->GetState() == ERenderMasterTransformAssistantState::Failed)
        {
            return TransformAssistant->GetSummaryText();
        }
    }
    if (LastAssistantAction == ELastAssistantAction::Material && MaterialAssistant.IsValid())
    {
        if (MaterialAssistant->IsSearching())
        {
            return MaterialAssistant->GetSummaryText();
        }
        if (MaterialAssistant->GetState() == ERenderMasterMaterialAssistantState::Proposed)
        {
            return LOCTEXT("ProposedReply", "I found a catalog-verified material candidate. Review the exact Actor, slot, current material, proposed material, and similarity before approving.");
        }
        if (MaterialAssistant->GetState() == ERenderMasterMaterialAssistantState::Unresolved
            || MaterialAssistant->GetState() == ERenderMasterMaterialAssistantState::Failed)
        {
            return MaterialAssistant->GetSummaryText();
        }
    }
    return FText::FromString(AssistantReply);
}

FText SRenderMasterWorkspace::GetLastRequest() const
{
    return FText::FromString(LastRequest);
}

FText SRenderMasterWorkspace::GetTargetMaterialSlotText() const
{
    FString Error;
    UStaticMeshComponent* Component = GetSingleSelectedStaticMeshComponent(Error);
    if (Component == nullptr || Component->GetStaticMesh() == nullptr)
    {
        return LOCTEXT("ChooseActorForSlot", "Select one Static Mesh Actor first");
    }

    const TArray<FStaticMaterial>& Materials = Component->GetStaticMesh()->GetStaticMaterials();
    int32 SlotIndex = INDEX_NONE;
    if (Materials.Num() == 1)
    {
        SlotIndex = 0;
    }
    else if (SelectedSlotComponent.Get() == Component
        && Materials.IsValidIndex(SelectedMaterialSlotIndex))
    {
        SlotIndex = SelectedMaterialSlotIndex;
    }

    if (!Materials.IsValidIndex(SlotIndex))
    {
        return FText::FromString(FString::Printf(
            TEXT("Choose one of %d material slots..."),
            Materials.Num()));
    }

    const FName SlotName = Materials[SlotIndex].MaterialSlotName;
    const FString Name = SlotName.IsNone()
        ? FString::Printf(TEXT("Material_%d"), SlotIndex)
        : SlotName.ToString();
    const TCHAR* SelectionMode = Materials.Num() == 1 ? TEXT("automatic") : TEXT("selected");
    return FText::FromString(FString::Printf(
        TEXT("%d — %s (%s)"),
        SlotIndex,
        *Name,
        SelectionMode));
}

EVisibility SRenderMasterWorkspace::GetLastRequestVisibility() const
{
    return LastRequest.IsEmpty() ? EVisibility::Collapsed : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetProposalVisibility() const
{
    if (!MaterialAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterMaterialAssistantState State = MaterialAssistant->GetState();
    return State == ERenderMasterMaterialAssistantState::Ready
        || State == ERenderMasterMaterialAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetApplyVisibility() const
{
    return MaterialAssistant.IsValid() && MaterialAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetRejectVisibility() const
{
    if (!MaterialAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterMaterialAssistantState State = MaterialAssistant->GetState();
    return State == ERenderMasterMaterialAssistantState::Searching
        || State == ERenderMasterMaterialAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetTransformProposalVisibility() const
{
    if (!TransformAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterTransformAssistantState State = TransformAssistant->GetState();
    return State == ERenderMasterTransformAssistantState::Ready
        || State == ERenderMasterTransformAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetTransformApplyVisibility() const
{
    return TransformAssistant.IsValid() && TransformAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetTransformRejectVisibility() const
{
    if (!TransformAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterTransformAssistantState State = TransformAssistant->GetState();
    return State == ERenderMasterTransformAssistantState::Planning
        || State == ERenderMasterTransformAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightProposalVisibility() const
{
    if (!LightAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightAssistantState State = LightAssistant->GetState();
    return State == ERenderMasterLightAssistantState::Ready
        || State == ERenderMasterLightAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLightApplyVisibility() const
{
    return LightAssistant.IsValid() && LightAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightRejectVisibility() const
{
    if (!LightAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightAssistantState State = LightAssistant->GetState();
    return State == ERenderMasterLightAssistantState::Planning
        || State == ERenderMasterLightAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightingRigProposalVisibility() const
{
    if (!LightingRigAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightingRigAssistantState State = LightingRigAssistant->GetState();
    return State == ERenderMasterLightingRigAssistantState::Ready
        || State == ERenderMasterLightingRigAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLightingRigApplyVisibility() const
{
    return LightingRigAssistant.IsValid() && LightingRigAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightingRigRejectVisibility() const
{
    if (!LightingRigAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightingRigAssistantState State = LightingRigAssistant->GetState();
    return State == ERenderMasterLightingRigAssistantState::Planning
        || State == ERenderMasterLightingRigAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightingRigReviewVisibility() const
{
    if (!LightingRigReviewAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightingRigReviewState State =
        LightingRigReviewAssistant->GetState();
    return State == ERenderMasterLightingRigReviewState::Ready
        || State == ERenderMasterLightingRigReviewState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLightingRigReviewApplyVisibility() const
{
    return LightingRigReviewAssistant.IsValid()
        && LightingRigReviewAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLightingRigReviewRejectVisibility() const
{
    if (!LightingRigReviewAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLightingRigReviewState State =
        LightingRigReviewAssistant->GetState();
    return State == ERenderMasterLightingRigReviewState::Capturing
        || State == ERenderMasterLightingRigReviewState::Evaluating
        || State == ERenderMasterLightingRigReviewState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetCameraProposalVisibility() const
{
    if (!CameraAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterCameraAssistantState State = CameraAssistant->GetState();
    return State == ERenderMasterCameraAssistantState::Ready
        || State == ERenderMasterCameraAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetCameraApplyVisibility() const
{
    return CameraAssistant.IsValid() && CameraAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetCameraRejectVisibility() const
{
    if (!CameraAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterCameraAssistantState State = CameraAssistant->GetState();
    return State == ERenderMasterCameraAssistantState::Planning
        || State == ERenderMasterCameraAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetCameraBatchProposalVisibility() const
{
    if (!CameraBatchAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterCameraBatchAssistantState State =
        CameraBatchAssistant->GetState();
    return State == ERenderMasterCameraBatchAssistantState::Ready
        || State == ERenderMasterCameraBatchAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetCameraBatchApplyVisibility() const
{
    return CameraBatchAssistant.IsValid() && CameraBatchAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetCameraBatchRejectVisibility() const
{
    if (!CameraBatchAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterCameraBatchAssistantState State =
        CameraBatchAssistant->GetState();
    return State == ERenderMasterCameraBatchAssistantState::Planning
        || State == ERenderMasterCameraBatchAssistantState::Proposed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLevelProfilingVisibility() const
{
    if (!LevelProfilingAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLevelProfilingState State =
        LevelProfilingAssistant->GetState();
    return State == ERenderMasterLevelProfilingState::Ready
        || State == ERenderMasterLevelProfilingState::Cancelled
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLevelProfilingDismissVisibility() const
{
    return LevelProfilingAssistant.IsValid()
        && LevelProfilingAssistant->GetState()
            != ERenderMasterLevelProfilingState::Ready
        && LevelProfilingAssistant->GetState()
            != ERenderMasterLevelProfilingState::Cancelled
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowVerificationVisibility() const
{
    if (!ShadowVerificationAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterShadowVerificationState State =
        ShadowVerificationAssistant->GetState();
    return State == ERenderMasterShadowVerificationState::Ready
        || State == ERenderMasterShadowVerificationState::Dismissed
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetShadowVerificationStartVisibility() const
{
    return ShadowVerificationAssistant.IsValid()
        && ShadowVerificationAssistant->GetState()
            == ERenderMasterShadowVerificationState::Armed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowVerificationKeepVisibility() const
{
    return ShadowVerificationAssistant.IsValid()
        && ShadowVerificationAssistant->CanKeepAppliedOptimization()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowVerificationRollbackVisibility() const
{
    return ShadowVerificationAssistant.IsValid()
        && ShadowVerificationAssistant->CanRollbackAppliedOptimization()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowVerificationDismissVisibility() const
{
    if (!ShadowVerificationAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterShadowVerificationState State =
        ShadowVerificationAssistant->GetState();
    return State == ERenderMasterShadowVerificationState::Ready
        || State == ERenderMasterShadowVerificationState::Dismissed
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLevelAuditVisibility() const
{
    if (!LevelAuditAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLevelAuditState State = LevelAuditAssistant->GetState();
    return State == ERenderMasterLevelAuditState::Ready
        || State == ERenderMasterLevelAuditState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetLevelAuditApplyVisibility() const
{
    return LevelAuditAssistant.IsValid() && LevelAuditAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetLevelAuditRejectVisibility() const
{
    if (!LevelAuditAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterLevelAuditState State = LevelAuditAssistant->GetState();
    return State == ERenderMasterLevelAuditState::Analyzing
        || State == ERenderMasterLevelAuditState::Proposed
        || State == ERenderMasterLevelAuditState::ReviewOnly
        || State == ERenderMasterLevelAuditState::Unresolved
        || State == ERenderMasterLevelAuditState::Failed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetPerformanceProposalVisibility() const
{
    if (!PerformanceAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterPerformanceAssistantState State =
        PerformanceAssistant->GetState();
    return State == ERenderMasterPerformanceAssistantState::Ready
        || State == ERenderMasterPerformanceAssistantState::Rejected
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetPerformanceApplyVisibility() const
{
    return PerformanceAssistant.IsValid() && PerformanceAssistant->CanApply()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetPerformanceRejectVisibility() const
{
    if (!PerformanceAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterPerformanceAssistantState State =
        PerformanceAssistant->GetState();
    return State == ERenderMasterPerformanceAssistantState::Planning
        || State == ERenderMasterPerformanceAssistantState::Proposed
        || State == ERenderMasterPerformanceAssistantState::ReviewOnly
        || State == ERenderMasterPerformanceAssistantState::Unresolved
        || State == ERenderMasterPerformanceAssistantState::Failed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetRuntimePerformanceVisibility() const
{
    if (!RuntimePerformanceAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterRuntimePerformanceState State =
        RuntimePerformanceAssistant->GetState();
    return State == ERenderMasterRuntimePerformanceState::Ready
        || State == ERenderMasterRuntimePerformanceState::Dismissed
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetRuntimePerformanceDismissVisibility() const
{
    return RuntimePerformanceAssistant.IsValid()
        && RuntimePerformanceAssistant->GetState()
            != ERenderMasterRuntimePerformanceState::Ready
        && RuntimePerformanceAssistant->GetState()
            != ERenderMasterRuntimePerformanceState::Dismissed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetInsightsGpuVisibility() const
{
    if (!InsightsGpuAssistant.IsValid()) return EVisibility::Collapsed;
    const ERenderMasterInsightsGpuState State = InsightsGpuAssistant->GetState();
    return State == ERenderMasterInsightsGpuState::Ready
        || State == ERenderMasterInsightsGpuState::Dismissed
        ? EVisibility::Collapsed
        : EVisibility::Visible;
}

EVisibility SRenderMasterWorkspace::GetInsightsGpuOpenVisibility() const
{
    return InsightsGpuAssistant.IsValid()
        && InsightsGpuAssistant->CanOpenTrace()
        && !InsightsGpuAssistant->IsBusy()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetInsightsGpuBaselineOpenVisibility() const
{
    return InsightsGpuAssistant.IsValid()
        && InsightsGpuAssistant->CanOpenBaselineTrace()
        && !InsightsGpuAssistant->IsBusy()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetInsightsGpuDismissVisibility() const
{
    return InsightsGpuAssistant.IsValid()
        && InsightsGpuAssistant->GetState()
            != ERenderMasterInsightsGpuState::Ready
        && InsightsGpuAssistant->GetState()
            != ERenderMasterInsightsGpuState::Dismissed
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowOptimizationApplyVisibility() const
{
    return InsightsGpuAssistant.IsValid()
        && InsightsGpuAssistant->CanApplyShadowOptimization()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

EVisibility SRenderMasterWorkspace::GetShadowOptimizationRejectVisibility() const
{
    return InsightsGpuAssistant.IsValid()
        && InsightsGpuAssistant->CanRejectShadowOptimization()
        ? EVisibility::Visible
        : EVisibility::Collapsed;
}

bool SRenderMasterWorkspace::GetSelectedActorsForTransform(
    TArray<AActor*>& OutActors,
    FString& OutError) const
{
    OutActors.Reset();
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return false;
    }
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        if (AActor* Actor = Cast<AActor>(*Iterator)) OutActors.Add(Actor);
    }
    if (OutActors.IsEmpty() || OutActors.Num() > 32)
    {
        OutError = TEXT("Select between one and 32 Actors before preparing a Transform action.");
        OutActors.Reset();
        return false;
    }
    OutActors.Sort([](const AActor& Left, const AActor& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    return true;
}

bool SRenderMasterWorkspace::GetSelectedLights(
    TArray<ALight*>& OutLights,
    FString& OutError) const
{
    OutLights.Reset();
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return false;
    }
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        AActor* Actor = Cast<AActor>(*Iterator);
        if (Actor == nullptr) continue;
        ALight* LightActor = Cast<ALight>(Actor);
        if (LightActor == nullptr || LightActor->GetLightComponent() == nullptr)
        {
            OutError = TEXT("Every selected Actor must be a supported Directional, Point, Spot, or Rect Light.");
            OutLights.Reset();
            return false;
        }
        OutLights.Add(LightActor);
    }
    if (OutLights.IsEmpty() || OutLights.Num() > 16)
    {
        OutError = TEXT("Select between one and 16 supported Light Actors before preparing a light action.");
        OutLights.Reset();
        return false;
    }
    OutLights.Sort([](const ALight& Left, const ALight& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    return true;
}

bool SRenderMasterWorkspace::GetSelectedLightingRig(
    AActor*& OutSubject,
    ACameraActor*& OutCamera,
    TArray<ALight*>& OutLights,
    FString& OutError) const
{
    OutSubject = nullptr;
    OutCamera = nullptr;
    OutLights.Reset();
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return false;
    }
    int32 ActorCount = 0;
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        AActor* Actor = Cast<AActor>(*Iterator);
        if (Actor == nullptr) continue;
        ++ActorCount;
        if (ALight* Light = Cast<ALight>(Actor))
        {
            OutLights.Add(Light);
        }
        else if (ACameraActor* CameraActor = Cast<ACameraActor>(Actor))
        {
            if (OutCamera != nullptr)
            {
                OutError = TEXT("Select exactly one Camera or Cine Camera for a lighting rig.");
                return false;
            }
            OutCamera = CameraActor;
        }
        else
        {
            if (OutSubject != nullptr)
            {
                OutError = TEXT("Select exactly one non-Light, non-Camera subject Actor for a lighting rig.");
                return false;
            }
            OutSubject = Actor;
        }
    }
    if (ActorCount != 5 || OutSubject == nullptr || OutCamera == nullptr
        || OutLights.Num() != 3)
    {
        OutError = TEXT("Select exactly five Actors: one subject, one perspective Camera/Cine Camera, and three Movable Point/Spot/Rect Lights.");
        OutSubject = nullptr;
        OutCamera = nullptr;
        OutLights.Reset();
        return false;
    }
    OutLights.Sort([](const ALight& Left, const ALight& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    return true;
}

AActor* SRenderMasterWorkspace::GetSingleSelectedActor(FString& OutError) const
{
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return nullptr;
    }

    AActor* SelectedActor = nullptr;
    int32 SelectedActorCount = 0;
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        if (AActor* Actor = Cast<AActor>(*Iterator))
        {
            SelectedActor = Actor;
            ++SelectedActorCount;
        }
    }
    if (SelectedActorCount != 1 || SelectedActor == nullptr)
    {
        OutError = TEXT("Select exactly one Actor before preparing this action.");
        return nullptr;
    }
    return SelectedActor;
}

ALight* SRenderMasterWorkspace::GetSingleSelectedLight(FString& OutError) const
{
    AActor* Actor = GetSingleSelectedActor(OutError);
    if (Actor == nullptr) return nullptr;
    ALight* LightActor = Cast<ALight>(Actor);
    if (LightActor == nullptr || LightActor->GetLightComponent() == nullptr)
    {
        OutError = TEXT("Select exactly one Directional, Point, Spot, or Rect Light Actor before preparing a light action.");
        return nullptr;
    }
    return LightActor;
}

ACameraActor* SRenderMasterWorkspace::GetSingleSelectedCamera(FString& OutError) const
{
    AActor* Actor = GetSingleSelectedActor(OutError);
    if (Actor == nullptr) return nullptr;
    ACameraActor* CameraActor = Cast<ACameraActor>(Actor);
    if (CameraActor == nullptr || CameraActor->GetCameraComponent() == nullptr)
    {
        OutError = TEXT("Select exactly one Camera Actor or Cine Camera Actor before preparing a camera action.");
        return nullptr;
    }
    return CameraActor;
}

bool SRenderMasterWorkspace::GetSelectedCameras(
    TArray<ACameraActor*>& OutCameras,
    FString& OutError) const
{
    OutCameras.Reset();
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return false;
    }
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        AActor* Actor = Cast<AActor>(*Iterator);
        ACameraActor* Camera = Cast<ACameraActor>(Actor);
        if (Camera == nullptr || Camera->GetCameraComponent() == nullptr)
        {
            OutError = TEXT("Select only Camera Actor or Cine Camera Actor targets for a camera action.");
            OutCameras.Reset();
            return false;
        }
        OutCameras.Add(Camera);
    }
    if (OutCameras.IsEmpty() || OutCameras.Num() > 16)
    {
        OutError = TEXT("Select between one and 16 Camera or Cine Camera Actors.");
        OutCameras.Reset();
        return false;
    }
    OutCameras.Sort([](const ACameraActor& Left, const ACameraActor& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    return true;
}

bool SRenderMasterWorkspace::GetSelectedStaticMeshActors(
    TArray<AStaticMeshActor*>& OutActors,
    FString& OutError) const
{
    OutActors.Reset();
    if (GEditor == nullptr || GEditor->GetSelectedActors() == nullptr)
    {
        OutError = TEXT("The Unreal selection service is unavailable.");
        return false;
    }
    for (FSelectionIterator Iterator(*GEditor->GetSelectedActors()); Iterator; ++Iterator)
    {
        AActor* Actor = Cast<AActor>(*Iterator);
        AStaticMeshActor* StaticMeshActor = Cast<AStaticMeshActor>(Actor);
        if (StaticMeshActor == nullptr
            || StaticMeshActor->GetClass() != AStaticMeshActor::StaticClass()
            || StaticMeshActor->GetStaticMeshComponent() == nullptr
            || StaticMeshActor->GetStaticMeshComponent()->GetStaticMesh() == nullptr)
        {
            OutError = TEXT("Select only native StaticMeshActors with valid Static Mesh assets for this performance review.");
            OutActors.Reset();
            return false;
        }
        OutActors.Add(StaticMeshActor);
    }
    if (OutActors.IsEmpty() || OutActors.Num() > 32)
    {
        OutError = TEXT("Select between one and 32 StaticMeshActors for a performance review.");
        OutActors.Reset();
        return false;
    }
    OutActors.Sort([](const AStaticMeshActor& Left, const AStaticMeshActor& Right)
    {
        return Left.GetPathName() < Right.GetPathName();
    });
    return true;
}

UStaticMeshComponent* SRenderMasterWorkspace::GetSingleSelectedStaticMeshComponent(
    FString& OutError) const
{
    AActor* SelectedActor = GetSingleSelectedActor(OutError);
    if (SelectedActor == nullptr) return nullptr;

    TArray<UStaticMeshComponent*> Components;
    SelectedActor->GetComponents<UStaticMeshComponent>(Components);
    Components.RemoveAll([](const UStaticMeshComponent* Component)
    {
        return Component == nullptr || Component->GetStaticMesh() == nullptr;
    });
    if (Components.Num() != 1)
    {
        OutError = TEXT("The selected Actor must contain exactly one valid Static Mesh Component for this first material capability.");
        return nullptr;
    }
    return Components[0];
}

bool SRenderMasterWorkspace::ResolveTargetMaterialSlot(
    UStaticMeshComponent* Component,
    int32& OutSlotIndex,
    FString& OutError) const
{
    OutSlotIndex = INDEX_NONE;
    if (Component == nullptr || Component->GetStaticMesh() == nullptr)
    {
        OutError = TEXT("Select one Actor with one valid Static Mesh Component.");
        return false;
    }

    const TArray<FStaticMaterial>& Materials = Component->GetStaticMesh()->GetStaticMaterials();
    if (Materials.IsEmpty())
    {
        OutError = TEXT("The selected Static Mesh has no material slots.");
        return false;
    }
    if (Materials.Num() == 1)
    {
        OutSlotIndex = 0;
        return true;
    }
    if (SelectedSlotComponent.Get() != Component
        || !Materials.IsValidIndex(SelectedMaterialSlotIndex))
    {
        OutError = TEXT("Choose the exact target material slot before preparing this multi-slot action.");
        return false;
    }

    OutSlotIndex = SelectedMaterialSlotIndex;
    return true;
}

void SRenderMasterWorkspace::SelectTargetMaterialSlot(
    UStaticMeshComponent* Component,
    int32 SlotIndex)
{
    if (Component == nullptr
        || Component->GetStaticMesh() == nullptr
        || !Component->GetStaticMesh()->GetStaticMaterials().IsValidIndex(SlotIndex))
    {
        return;
    }

    const bool TargetChanged = SelectedSlotComponent.Get() != Component
        || SelectedMaterialSlotIndex != SlotIndex;
    SelectedSlotComponent = Component;
    SelectedMaterialSlotIndex = SlotIndex;
    if (TargetChanged && MaterialAssistant.IsValid())
    {
        const ERenderMasterMaterialAssistantState State = MaterialAssistant->GetState();
        if (State == ERenderMasterMaterialAssistantState::Searching
            || State == ERenderMasterMaterialAssistantState::Proposed)
        {
            MaterialAssistant->RejectProposal();
            AssistantReply = TEXT("The target material slot changed, so the previous proposal was discarded. Prepare a new action for this slot.");
        }
    }
}

#undef LOCTEXT_NAMESPACE
