using UnrealBuildTool;

public class RenderMasterBotEditor : ModuleRules
{
    public RenderMasterBotEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "ApplicationCore",
            "AssetRegistry",
            "Core",
            "CoreUObject",
            "DesktopPlatform",
            "Engine",
            "InputCore",
            "Json",
            "Projects",
            "Slate",
            "SlateCore",
            "ToolMenus",
            "UnrealEd",
            "WorkspaceMenuStructure",
        });
    }
}
