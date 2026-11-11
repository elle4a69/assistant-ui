"""
Generate AI_Booking_Relay_Master shortcut and plist definitions with dynamic URL attachment ranges
and robust token variable bindings.
"""

import os
import plistlib
import shutil

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PLIST_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay_Master.plist")
SHORTCUT_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay_Master.shortcut")


def build_or_patch_workflow():
    with open(PLIST_PATH, "rb") as f:
        workflow = plistlib.load(f)

    actions = workflow["WFWorkflowActions"]

    # 1. Action 0: Token Text
    action_0_uuid = actions[0]["WFWorkflowActionParameters"]["UUID"]
    action_0_name = actions[0]["WFWorkflowActionParameters"].get("CustomOutputName", "Token Text")

    # 2. Action 1: Set Variable Tenant Token with explicit WFInput binding to Action 0
    actions[1]["WFWorkflowActionParameters"]["WFInput"] = {
        "Value": {
            "OutputName": action_0_name,
            "OutputUUID": action_0_uuid,
            "Type": "ActionOutput",
        },
        "WFSerializationType": "WFTextTokenAttachment",
    }

    # 3. Action 6: Outbound GET with dynamically computed attachment range for token
    url_prefix = "https://assistant-ui-hub.fly.dev/webhook/outbound?token="
    range_key = f"{{{len(url_prefix)}, 1}}"

    actions[6]["WFWorkflowActionParameters"]["WFURL"] = {
        "Value": {
            "attachmentsByRange": {
                range_key: {
                    "Type": "Variable",
                    "VariableName": "Tenant Token",
                }
            },
            "string": f"{url_prefix}\ufffc",
        },
        "WFSerializationType": "WFTextTokenString",
    }

    # Write XML plist
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_XML)
    print(f"Saved XML plist: {PLIST_PATH}")

    # Write binary shortcut
    with open(SHORTCUT_PATH, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_BINARY)
    print(f"Saved binary shortcut: {SHORTCUT_PATH}")

    # Copy to distribution directories
    dest_dirs = [
        os.path.join(REPO_ROOT, "frontend", "public"),
        os.path.join(REPO_ROOT, "frontend", "dist"),
        os.path.join(REPO_ROOT, "data", "static"),
    ]

    for d in dest_dirs:
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(PLIST_PATH, os.path.join(d, "AI_Booking_Relay_Master.plist"))
        shutil.copyfile(SHORTCUT_PATH, os.path.join(d, "AI_Booking_Relay_Master.shortcut"))
        print(f"Copied artifacts to {d}")


if __name__ == "__main__":
    build_or_patch_workflow()
