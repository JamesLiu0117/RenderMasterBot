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
            "CinematicCamera",
            "Core",
            "CoreUObject",
            "DesktopPlatform",
            "Engine",
            "InputCore",
            "Json",
            "Projects",
            "RenderCore",
            "RHI",
            "Slate",
            "SlateCore",
            "ToolMenus",
            "TraceServices",
            "UnrealEd",
            "WorkspaceMenuStructure",
        });
    }
}
