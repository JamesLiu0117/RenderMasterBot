#include "SRenderMasterPanel.h"

#include "DesktopPlatformModule.h"
#include "Framework/Application/SlateApplication.h"
#include "IDesktopPlatform.h"
#include "Misc/MessageDialog.h"
#include "Misc/Paths.h"
#include "RenderMasterWorkflowController.h"
#include "Styling/AppStyle.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SMultiLineEditableTextBox.h"
#include "Widgets/Input/SNumericEntryBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SGridPanel.h"
#include "Widgets/Layout/SScaleBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Notifications/SProgressBar.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "RenderMasterBotPanel"

namespace
{
constexpr float CardPadding = 14.0f;

TSharedRef<SWidget> SectionTitle(const FText& Title, const FText& Subtitle)
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

void SRenderMasterPanel::Construct(const FArguments& InArgs)
{
    Controller = InArgs._Controller;
    check(Controller.IsValid());

    ChildSlot
    [
        SNew(SBorder)
        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Background")))
        .Padding(0.0f)
        [
            SNew(SScrollBox)
            + SScrollBox::Slot()
            [
                SNew(SVerticalBox)

                + SVerticalBox::Slot().AutoHeight().Padding(24.0f, 22.0f, 24.0f, 10.0f)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot().FillWidth(1.0f).VAlign(VAlign_Center)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(STextBlock)
                            .Text(LOCTEXT("Title", "Render & Evaluate"))
                            .Font(FAppStyle::GetFontStyle(TEXT("HeadingExtraLarge")))
                        ]
                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 4.0f, 0.0f, 0.0f)
                        [
                            SNew(STextBlock)
                            .Text(LOCTEXT("Subtitle", "Local AI scene planning, Unreal rendering, and visual correction in one editor workflow"))
                            .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                        ]
                    ]
                    + SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
                    [
                        SNew(SBorder)
                        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Header")))
                        .BorderBackgroundColor_Lambda([Controller = Controller]() { return Controller->GetStatusColor(); })
                        .Padding(FMargin(14.0f, 8.0f))
                        [
                            SNew(SVerticalBox)
                            + SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)
                            [
                                SNew(STextBlock)
                                .Text_Lambda([Controller = Controller]() { return Controller->GetStatusText(); })
                                .Font(FAppStyle::GetFontStyle(TEXT("HeadingSmall")))
                            ]
                            + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 2.0f, 0.0f, 0.0f).HAlign(HAlign_Center)
                            [
                                SNew(STextBlock)
                                .Text_Lambda([Controller = Controller]() { return Controller->GetStageText(); })
                            ]
                        ]
                    ]
                ]

                + SVerticalBox::Slot().AutoHeight().Padding(24.0f, 8.0f)
                [
                    SNew(SProgressBar)
                    .Percent_Lambda([Controller = Controller]() { return TOptional<float>(Controller->GetProgress()); })
                    .FillColorAndOpacity_Lambda([Controller = Controller]() { return Controller->GetStatusColor(); })
                ]

                + SVerticalBox::Slot().AutoHeight().Padding(24.0f, 6.0f, 24.0f, 16.0f)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 5.0f, 0.0f)[MakeStageChip(LOCTEXT("Retrieve", "1  Retrieve"), TEXT("retrieval"))]
                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 5.0f, 0.0f)[MakeStageChip(LOCTEXT("Plan", "2  Plan"), TEXT("planning"))]
                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 5.0f, 0.0f)[MakeStageChip(LOCTEXT("Validate", "3  Validate"), TEXT("preflight"))]
                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 5.0f, 0.0f)[MakeStageChip(LOCTEXT("Render", "4  Render"), TEXT("rendering"))]
                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 5.0f, 0.0f)[MakeStageChip(LOCTEXT("Evaluate", "5  Evaluate"), TEXT("evaluation"))]
                    + SHorizontalBox::Slot().FillWidth(1.0f)[MakeStageChip(LOCTEXT("Correct", "6  Correct"), TEXT("correction"))]
                ]

                + SVerticalBox::Slot().AutoHeight().Padding(24.0f, 0.0f, 24.0f, 24.0f)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot().FillWidth(0.47f).Padding(0.0f, 0.0f, 8.0f, 0.0f)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(CardPadding)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()[SectionTitle(LOCTEXT("RequestTitle", "Render request"), LOCTEXT("RequestHelp", "Describe the scene or product shot. The prompt is written to a UTF-8 file instead of the command line."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(SBox).HeightOverride(150.0f)
                                    [
                                        SAssignNew(PromptBox, SMultiLineEditableTextBox)
                                        .HintText(LOCTEXT("PromptHint", "Example: Create a studio product render of a dark walnut door with soft rim lighting..."))
                                        .AutoWrapText(true)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
                                    [
                                        SNew(STextBlock).Text(LOCTEXT("Iterations", "Maximum correction passes"))
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth().Padding(10.0f, 0.0f).VAlign(VAlign_Center)
                                    [
                                        SNew(SNumericEntryBox<int32>)
                                        .MinValue(1).MaxValue(5)
                                        .Value_Lambda([this]() { return TOptional<int32>(MaxIterations); })
                                        .OnValueChanged_Lambda([this](int32 Value) { MaxIterations = Value; })
                                        .MinDesiredValueWidth(56.0f)
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().FillWidth(1.0f).Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .ButtonStyle(FAppStyle::Get(), TEXT("PrimaryButton"))
                                        .IsEnabled_Lambda([Controller = Controller]() { return Controller->CanStart(); })
                                        .OnClicked(this, &SRenderMasterPanel::OnRunClicked)
                                        .ContentPadding(FMargin(14.0f, 7.0f))
                                        [
                                            SNew(STextBlock).Text(LOCTEXT("Run", "Start workflow")).Justification(ETextJustify::Center)
                                        ]
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .IsEnabled_Lambda([Controller = Controller]() { return Controller->IsRunning(); })
                                        .OnClicked(this, &SRenderMasterPanel::OnCancelClicked)
                                        .ContentPadding(FMargin(14.0f, 7.0f))
                                        [SNew(STextBlock).Text(LOCTEXT("Cancel", "Cancel"))]
                                    ]
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SExpandableArea)
                            .InitiallyCollapsed(true)
                            .AreaTitle(LOCTEXT("RuntimeSettings", "Runtime settings"))
                            .BodyContent()
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight().Padding(10.0f, 4.0f)
                                [MakePathSetting(LOCTEXT("Python", "Python executable"), [Controller = Controller]() { return Controller->GetPythonExecutable(); }, [Controller = Controller](const FString& Value) { Controller->SetPythonExecutable(Value); }, [this]() { BrowsePython(); })]
                                + SVerticalBox::Slot().AutoHeight().Padding(10.0f, 4.0f)
                                [MakePathSetting(LOCTEXT("Catalog", "Asset catalog"), [Controller = Controller]() { return Controller->GetAssetCatalog(); }, [Controller = Controller](const FString& Value) { Controller->SetAssetCatalog(Value); }, [this]() { BrowseAssetCatalog(); })]
                                + SVerticalBox::Slot().AutoHeight().Padding(10.0f, 4.0f)
                                [MakePathSetting(LOCTEXT("WorkflowRoot", "Workflow output root"), [Controller = Controller]() { return Controller->GetWorkflowRoot(); }, [Controller = Controller](const FString& Value) { Controller->SetWorkflowRoot(Value); }, [this]() { BrowseWorkflowRoot(); })]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(CardPadding)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()[SectionTitle(LOCTEXT("LogTitle", "Live process log"), LOCTEXT("LogHelp", "Python and Unreal child-process output, retained for the current run."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(SBox).HeightOverride(210.0f)
                                    [
                                        SNew(SScrollBox).Orientation(Orient_Vertical)
                                        + SScrollBox::Slot()
                                        [
                                            SNew(STextBlock)
                                            .Text_Lambda([Controller = Controller]() { return Controller->GetLogText(); })
                                            .Font(FAppStyle::GetFontStyle(TEXT("Mono")))
                                            .AutoWrapText(true)
                                        ]
                                    ]
                                ]
                            ]
                        ]
                    ]

                    + SHorizontalBox::Slot().FillWidth(0.53f).Padding(8.0f, 0.0f, 0.0f, 0.0f)
                    [
                        SNew(SVerticalBox)
                        + SVerticalBox::Slot().AutoHeight()
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(CardPadding)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()[SectionTitle(LOCTEXT("PreviewTitle", "Latest rendered preview"), LOCTEXT("PreviewHelp", "Updates automatically when a new beauty.png is produced."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(SBox).HeightOverride(430.0f)
                                    [
                                        SNew(SBorder)
                                        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Recessed")))
                                        .Padding(8.0f)
                                        [
                                            SNew(SOverlay)
                                            + SOverlay::Slot()
                                            [
                                                SNew(SScaleBox).Stretch(EStretch::ScaleToFit)
                                                [
                                                    SNew(SImage)
                                                    .Image_Lambda([Controller = Controller]() { return Controller->GetPreviewBrush(); })
                                                ]
                                            ]
                                            + SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Center)
                                            [
                                                SNew(STextBlock)
                                                .Text(LOCTEXT("NoPreview", "No preview yet\nStart a workflow to render the first image"))
                                                .Justification(ETextJustify::Center)
                                                .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                                                .Visibility_Lambda([Controller = Controller]() { return Controller->HasPreview() ? EVisibility::Collapsed : EVisibility::Visible; })
                                            ]
                                        ]
                                    ]
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Controller = Controller]() { return Controller->GetStatisticsText(); })
                                    .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                                ]
                            ]
                        ]

                        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                        [
                            SNew(SBorder)
                            .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Panel")))
                            .Padding(CardPadding)
                            [
                                SNew(SVerticalBox)
                                + SVerticalBox::Slot().AutoHeight()[SectionTitle(LOCTEXT("EvaluationTitle", "Visual evaluation"), LOCTEXT("EvaluationHelp", "Qwen vision verdict and correction guidance for the latest iteration."))]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Controller = Controller]() { return Controller->GetEvaluationText(); })
                                    .AutoWrapText(true)
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 10.0f, 0.0f, 0.0f)
                                [
                                    SNew(STextBlock)
                                    .Text_Lambda([Controller = Controller]() { return Controller->GetDetailText(); })
                                    .ColorAndOpacity(FSlateColor::UseSubduedForeground())
                                ]
                                + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 12.0f, 0.0f, 0.0f)
                                [
                                    SNew(SHorizontalBox)
                                    + SHorizontalBox::Slot().AutoWidth().Padding(0.0f, 0.0f, 6.0f, 0.0f)
                                    [
                                        SNew(SButton)
                                        .Text(LOCTEXT("OpenFolder", "Open workflow folder"))
                                        .IsEnabled_Lambda([Controller = Controller]() { return Controller->HasWorkflowFolder(); })
                                        .OnClicked_Lambda([Controller = Controller]() { Controller->OpenWorkflowFolder(); return FReply::Handled(); })
                                    ]
                                    + SHorizontalBox::Slot().AutoWidth()
                                    [
                                        SNew(SButton)
                                        .Text(LOCTEXT("OpenPreview", "Open preview"))
                                        .IsEnabled_Lambda([Controller = Controller]() { return Controller->HasPreview(); })
                                        .OnClicked_Lambda([Controller = Controller]() { Controller->OpenPreview(); return FReply::Handled(); })
                                    ]
                                ]
                            ]
                        ]
                    ]
                ]
            ]
        ]
    ];
}

TSharedRef<SWidget> SRenderMasterPanel::MakeStageChip(const FText& Label, const FString& StageName)
{
    return SNew(SBorder)
        .BorderImage(FAppStyle::GetBrush(TEXT("Brushes.Header")))
        .BorderBackgroundColor_Lambda([Controller = Controller, StageName]() { return Controller->GetStageColor(StageName); })
        .Padding(FMargin(8.0f, 7.0f))
        [
            SNew(STextBlock).Text(Label).Justification(ETextJustify::Center)
        ];
}

TSharedRef<SWidget> SRenderMasterPanel::MakePathSetting(
    const FText& Label,
    TFunction<FString()> Getter,
    TFunction<void(const FString&)> Setter,
    TFunction<void()> BrowseAction)
{
    return SNew(SVerticalBox)
        + SVerticalBox::Slot().AutoHeight()
        [SNew(STextBlock).Text(Label).ColorAndOpacity(FSlateColor::UseSubduedForeground())]
        + SVerticalBox::Slot().AutoHeight().Padding(0.0f, 3.0f, 0.0f, 0.0f)
        [
            SNew(SHorizontalBox)
            + SHorizontalBox::Slot().FillWidth(1.0f)
            [
                SNew(SEditableTextBox)
                .Text_Lambda([Getter]() { return FText::FromString(Getter()); })
                .OnTextCommitted_Lambda([Setter](const FText& Text, ETextCommit::Type) { Setter(Text.ToString()); })
            ]
            + SHorizontalBox::Slot().AutoWidth().Padding(5.0f, 0.0f, 0.0f, 0.0f)
            [
                SNew(SButton).Text(LOCTEXT("Browse", "Browse…")).OnClicked_Lambda([BrowseAction]() { BrowseAction(); return FReply::Handled(); })
            ]
        ];
}

FReply SRenderMasterPanel::OnRunClicked()
{
    Controller->Start(PromptBox->GetText().ToString(), MaxIterations);
    return FReply::Handled();
}

FReply SRenderMasterPanel::OnCancelClicked()
{
    if (FMessageDialog::Open(EAppMsgType::YesNo, LOCTEXT("ConfirmCancel", "Cancel the active workflow and its child Unreal render process?")) == EAppReturnType::Yes)
    {
        Controller->Cancel();
    }
    return FReply::Handled();
}

FReply SRenderMasterPanel::BrowsePython()
{
    TArray<FString> Files;
    IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
    if (DesktopPlatform != nullptr && DesktopPlatform->OpenFileDialog(
        FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr),
        LOCTEXT("ChoosePython", "Choose Python executable").ToString(),
        FPaths::GetPath(Controller->GetPythonExecutable()),
        FString(),
        TEXT("Python executable (*.exe)|*.exe|All files (*.*)|*.*"),
        EFileDialogFlags::None,
        Files) && !Files.IsEmpty())
    {
        Controller->SetPythonExecutable(Files[0]);
    }
    return FReply::Handled();
}

FReply SRenderMasterPanel::BrowseAssetCatalog()
{
    TArray<FString> Files;
    IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
    if (DesktopPlatform != nullptr && DesktopPlatform->OpenFileDialog(
        FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr),
        LOCTEXT("ChooseCatalog", "Choose asset_cards.json").ToString(),
        FPaths::GetPath(Controller->GetAssetCatalog()),
        FString(),
        TEXT("JSON files (*.json)|*.json|All files (*.*)|*.*"),
        EFileDialogFlags::None,
        Files) && !Files.IsEmpty())
    {
        Controller->SetAssetCatalog(Files[0]);
    }
    return FReply::Handled();
}

FReply SRenderMasterPanel::BrowseWorkflowRoot()
{
    FString Folder;
    IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
    if (DesktopPlatform != nullptr && DesktopPlatform->OpenDirectoryDialog(
        FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr),
        LOCTEXT("ChooseOutput", "Choose workflow output root").ToString(),
        Controller->GetWorkflowRoot(),
        Folder))
    {
        Controller->SetWorkflowRoot(Folder);
    }
    return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE
