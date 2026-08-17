#include "SRenderMasterWorkspace.h"

#include "Editor.h"
#include "Camera/CameraActor.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Selection.h"
#include "Engine/Light.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "RenderMasterCameraAssistant.h"
#include "RenderMasterWorkflowController.h"
#include "RenderMasterMaterialAssistant.h"
#include "RenderMasterLightAssistant.h"
#include "RenderMasterTransformAssistant.h"
#include "SRenderMasterPanel.h"
#include "Styling/AppStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SComboButton.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Input/SNumericEntryBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Layout/SWidgetSwitcher.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "RenderMasterBotWorkspace"

namespace
{
constexpr float WorkspacePadding = 22.0f;

TSharedRef<SWidget> WorkspaceSectionTitle(const FText& Title, TAttribute<FText> Subtitle)
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
    MaterialAssistant = MakeShared<FRenderMasterMaterialAssistant>(Controller);
    MaterialAssistant->Initialize();
    TransformAssistant = MakeShared<FRenderMasterTransformBatchAssistant>(Controller);
    TransformAssistant->Initialize();
    LightAssistant = MakeShared<FRenderMasterLightBatchAssistant>(Controller);
    LightAssistant->Initialize();
    CameraAssistant = MakeShared<FRenderMasterCameraAssistant>(Controller);
    CameraAssistant->Initialize();
    AssistantReply = TEXT("Tell me what you want to understand or change. I will inspect the current Unreal context before proposing an action.");

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
}

SRenderMasterWorkspace::~SRenderMasterWorkspace()
{
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
                                        SNew(SScrollBox)
                                        + SScrollBox::Slot().Padding(0.0f, 0.0f, 0.0f, 8.0f)
                                        [
                                            SNew(SBox)
                                            .Visibility(this, &SRenderMasterWorkspace::GetLastRequestVisibility)
                                            [
                                                MakeMessage(LOCTEXT("YouSpeaker", "YOU"), TAttribute<FText>::CreateSP(this, &SRenderMasterWorkspace::GetLastRequest), FLinearColor(0.55f, 0.42f, 0.92f))
                                            ]
                                        ]
                                        + SScrollBox::Slot().Padding(0.0f, 0.0f, 0.0f, 8.0f)
                                        [
                                            MakeMessage(LOCTEXT("AssistantSpeaker", "ASSISTANT"), TAttribute<FText>::CreateSP(this, &SRenderMasterWorkspace::GetAssistantReply), FLinearColor(0.16f, 0.58f, 0.95f))
                                        ]
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
                                        .ToolTipText(LOCTEXT("PrepareCameraHelp", "Prepare bounded Transform, lens, focus, or exposure-compensation properties for exactly one selected Camera Actor or Cine Camera Actor."))
                                        .IsEnabled_Lambda([this]() { return CanPrepareAssistantAction(); })
                                        .OnClicked(this, &SRenderMasterWorkspace::PrepareCameraAction)
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
                                    .Text(LOCTEXT("CapabilityList", "CONNECTED\n✓ Live project and actor-selection context\n✓ Explicit material-slot targeting\n✓ Catalog-backed project material recommendation\n✓ CC0 Poly Haven search and verified local cache\n✓ Hash-bound approval for 5-asset PBR import\n✓ Automatic catalog and Chroma synchronization\n✓ Approval-gated material override with Ctrl+Z Undo\n✓ Approval-gated 1–32 Actor world/local Transform with grouped Ctrl+Z Undo\n✓ Approval-gated 1–16 Light compatible group properties with grouped Ctrl+Z Undo\n✓ Camera and Cine Camera Transform, lens, focus, and exposure compensation with Ctrl+Z Undo\n✓ Schema-gated scene planning\n✓ Transient Unreal preview\n✓ Visual evaluation and bounded correction\n\nNOT CONNECTED YET\n○ Per-light role coordination and coordinated multi-camera editing\n○ Geometry-aware arrangement\n○ Performance diagnosis and optimization"))
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

FReply SRenderMasterWorkspace::PrepareCameraAction()
{
    const FString Prompt = AssistantPromptBox->GetText().ToString().TrimStartAndEnd();
    if (Prompt.IsEmpty())
    {
        AssistantReply = TEXT("Enter a numeric camera Transform, lens, focus, or exposure-compensation request before preparing an action. Nothing has been executed.");
        return FReply::Handled();
    }

    LastRequest = Prompt;
    LastAssistantAction = ELastAssistantAction::Camera;
    FString SelectionError;
    ACameraActor* CameraActor = GetSingleSelectedCamera(SelectionError);
    if (CameraActor == nullptr)
    {
        AssistantReply = SelectionError;
        return FReply::Handled();
    }
    if (CameraAssistant->StartProposal(Prompt, CameraActor))
    {
        AssistantReply = TEXT("I froze the selected camera identity, type, lens limits, Transform, focus, and exposure compensation, then started preparing a bounded proposal. No scene change has been applied.");
    }
    else
    {
        AssistantReply = CameraAssistant->GetSummaryText().ToString();
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

void SRenderMasterWorkspace::SetPage(int32 PageIndex)
{
    ActivePage = FMath::Clamp(PageIndex, 0, 1);
    if (PageSwitcher.IsValid()) PageSwitcher->SetActiveWidgetIndex(ActivePage);
}

bool SRenderMasterWorkspace::CanPrepareAssistantAction() const
{
    if (!Controller.IsValid()
        || !MaterialAssistant.IsValid()
        || !TransformAssistant.IsValid()
        || !LightAssistant.IsValid()
        || !CameraAssistant.IsValid())
    {
        return false;
    }
    const ERenderMasterMaterialAssistantState MaterialState = MaterialAssistant->GetState();
    const ERenderMasterTransformAssistantState TransformState = TransformAssistant->GetState();
    const ERenderMasterLightAssistantState LightState = LightAssistant->GetState();
    const ERenderMasterCameraAssistantState CameraState = CameraAssistant->GetState();
    const bool bMaterialPending = MaterialState == ERenderMasterMaterialAssistantState::Searching
        || MaterialState == ERenderMasterMaterialAssistantState::Importing
        || MaterialState == ERenderMasterMaterialAssistantState::Proposed;
    const bool bTransformPending = TransformState == ERenderMasterTransformAssistantState::Planning
        || TransformState == ERenderMasterTransformAssistantState::Proposed;
    const bool bLightPending = LightState == ERenderMasterLightAssistantState::Planning
        || LightState == ERenderMasterLightAssistantState::Proposed;
    const bool bCameraPending = CameraState == ERenderMasterCameraAssistantState::Planning
        || CameraState == ERenderMasterCameraAssistantState::Proposed;
    return Controller->CanStart()
        && MaterialAssistant->CanStart()
        && TransformAssistant->CanStart()
        && LightAssistant->CanStart()
        && CameraAssistant->CanStart()
        && !bMaterialPending
        && !bTransformPending
        && !bLightPending
        && !bCameraPending;
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
