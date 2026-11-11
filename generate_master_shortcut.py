"""
Generate AI_Booking_Relay_Master shortcut and plist definitions with Doorbell wake-up
filtering and dynamic URL attachment ranges for outbound dispatch.
"""

import copy
import os
import plistlib
import shutil

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PLIST_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay_Master.plist")
SHORTCUT_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay_Master.shortcut")


def build_master_workflow():
    with open(PLIST_PATH, "rb") as f:
        workflow = plistlib.load(f)

    orig_actions = workflow["WFWorkflowActions"]

    # 1. Action 0: Token Text
    act0 = copy.deepcopy(orig_actions[0])
    act0_uuid = act0["WFWorkflowActionParameters"]["UUID"]
    act0_name = act0["WFWorkflowActionParameters"].get("CustomOutputName", "Token Text")

    # 2. Action 1: Set Variable Tenant Token with explicit WFInput binding to Action 0
    act1 = copy.deepcopy(orig_actions[1])
    act1["WFWorkflowActionParameters"]["WFInput"] = {
        "Value": {
            "OutputName": act0_name,
            "OutputUUID": act0_uuid,
            "Type": "ActionOutput",
        },
        "WFSerializationType": "WFTextTokenAttachment",
    }

    # Group UUIDs for nested conditionals
    group_outer = "C142646E-D2DB-4E77-9523-702BD3A3BE0A"
    group_inner = "B3D5E4A1-8C12-4E90-A875-12DF8E94B3C2"

    # 3. Action 2: Outer If (Shortcut Input has any value, WFCondition = 100)
    act2 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_outer,
            "WFCondition": 100,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "Type": "ExtensionInput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }

    # 4. Action 3: Inner If (Shortcut Input Content does not contain DOORBELL, WFCondition = 999)
    act3 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_inner,
            "WFCondition": 999,
            "WFConditionalActionString": "DOORBELL",
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "Aggrandizements": [
                            {
                                "PropertyName": "Content",
                                "Type": "WFPropertyVariableAggrandizement",
                            }
                        ],
                        "Type": "ExtensionInput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }

    # 5. Action 4: Inbound Webhook POST
    # Find original inbound download action by matching URL
    orig_inbound = None
    for a in orig_actions:
        if a.get("WFWorkflowActionIdentifier") == "is.workflow.actions.downloadurl":
            params = a.get("WFWorkflowActionParameters", {})
            url = params.get("WFURL")
            if url and "webhook/inbound" in str(url):
                orig_inbound = copy.deepcopy(a)
                break
    if not orig_inbound:
        raise RuntimeError("Could not find original inbound downloadurl action in plist.")

    act4 = orig_inbound

    # 6. Action 5: Exit (Stop This Shortcut after inbound forward)
    act5 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.exit",
        "WFWorkflowActionParameters": {},
    }

    # 7. Action 6: End Inner If (WFControlFlowMode = 2)
    act6 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_inner,
            "WFControlFlowMode": 2,
        },
    }

    # 8. Action 7: End Outer If (WFControlFlowMode = 2)
    act7 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_outer,
            "WFControlFlowMode": 2,
        },
    }

    # 9. Outbound Dispatch Pipeline (21 actions)
    # Extract outbound actions: find the Doorbell Log POST action to the End If has_messages
    start_outbound_idx = None
    for i, a in enumerate(orig_actions):
        params = a.get("WFWorkflowActionParameters", {})
        if params.get("CustomOutputName") == "Wakeup Telemetry Response":
            start_outbound_idx = i
            break
    if start_outbound_idx is None:
        for i, a in enumerate(orig_actions):
            if a.get("WFWorkflowActionIdentifier") == "is.workflow.actions.downloadurl":
                params = a.get("WFWorkflowActionParameters", {})
                json_vals = params.get("WFJSONValues", {})
                if "DOORBELL_WOKE_UP" in str(json_vals):
                    start_outbound_idx = i
                    break

    if start_outbound_idx is None:
        raise RuntimeError("Could not find start of outbound pipeline in plist.")

    outbound_slice = [copy.deepcopy(a) for a in orig_actions[start_outbound_idx:start_outbound_idx + 21]]

    # Ensure dynamic URL range on GET Outbound action
    url_prefix = "https://assistant-ui-hub.fly.dev/webhook/outbound?token="
    range_key = f"{{{len(url_prefix)}, 1}}"
    for a in outbound_slice:
        if a.get("WFWorkflowActionIdentifier") == "is.workflow.actions.downloadurl":
            params = a.get("WFWorkflowActionParameters", {})
            if params.get("WFHTTPMethod") == "GET" and "webhook/outbound" in str(params.get("WFURL", "")):
                params["WFURL"] = {
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
                break

    all_actions = [act0, act1, act2, act3, act4, act5, act6, act7] + outbound_slice

    workflow["WFWorkflowActions"] = all_actions

    # Write XML plist
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_XML)
    print(f"Saved XML plist: {PLIST_PATH} ({len(all_actions)} actions)")

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
    build_master_workflow()
    from generate_unified_relay_shortcut import build_unified_relay_workflow
    build_unified_relay_workflow()
