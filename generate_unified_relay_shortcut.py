"""
Builder for AI_Booking_Relay.shortcut and AI_Booking_Relay.plist
Complies with Frank's unified iOS Shortcut specification & Universal Remote Action Framework:
1. Single Import Question: "Enter your Booking System Client Token:"
2. Device Health & Battery Telemetry:
   - Fetches Battery Level (getbatterylevel) and Wi-Fi Network Name (getwifinetworkname).
   - Bundles telemetry into /webhook/log, /webhook/inbound, and /webhook/outbound payloads.
3. Personal Safety & Emergency Panic Beacon:
   - Matches incoming trigger keywords: PANIC, CMD_LOCATE, SOS.
   - If triggered: pulls Current GPS Location (getcurrentlocation) with lat/lon/alt,
     POSTs to /api/v1/relay/panic with live coordinates, battery level, and halts execution cleanly.
4. Native Apple Calendar Event Creation on Device:
   - If outbound response contains 'calendar_event', executes is.workflow.actions.addnewevent
     with ShowWhenRun: False to write directly into the native iOS Apple Calendar.
5. Inbound/Outbound Smart Branching:
   - Outbound Mode: Doorbell SMS from Gateway (+61432172148 / 432172148), SYNC_AI, DOORBELL, or manual tap.
   - Inbound Mode: Customer SMS -> forwards to /webhook/inbound.
6. Reliability & Hardening:
   - Show When Run: False across all messaging and calendar actions.
   - Clean CustomOutputNames and validated UUID attachments.
"""

import copy
import os
import plistlib
import shutil
import uuid

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
BASE_PLIST_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay_Master.plist")
UNIFIED_PLIST_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay.plist")
UNIFIED_SHORTCUT_PATH = os.path.join(REPO_ROOT, "AI_Booking_Relay.shortcut")


def build_unified_relay_workflow():
    with open(BASE_PLIST_PATH, "rb") as f:
        base_workflow = plistlib.load(f)

    # Clean top-level workflow dictionary
    workflow = copy.deepcopy(base_workflow)
    workflow["WFWorkflowClientVersion"] = "2605.0.5"
    workflow["WFWorkflowHasShortcutInputVariables"] = True
    workflow["WFWorkflowIcon"] = {
        "WFWorkflowIconGlyphNumber": 59795,  # Message icon
        "WFWorkflowIconStartColor": 431817727,
    }

    # 1. Single Import Question
    workflow["WFWorkflowImportQuestions"] = [
        {
            "ActionIndex": 0,
            "Category": "Parameter",
            "DefaultValue": "CLIENT_01",
            "ParameterKey": "WFTextActionText",
            "Text": "Enter your Booking System Client Token:",
        }
    ]

    actions = []

    # Action 0: Token Text block
    act0_uuid = str(uuid.uuid4()).upper()
    act0 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Token Text",
            "UUID": act0_uuid,
            "WFTextActionText": "CLIENT_01",
        },
    }
    actions.append(act0)

    # Action 1: Set Variable Tenant Token
    act1 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.setvariable",
        "WFWorkflowActionParameters": {
            "WFInput": {
                "Value": {
                    "OutputName": "Token Text",
                    "OutputUUID": act0_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
            "WFVariableName": "Tenant Token",
        },
    }
    actions.append(act1)

    # Action 2: Get Battery Level
    act2_uuid = str(uuid.uuid4()).upper()
    act2 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getbatterylevel",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Battery Level",
            "UUID": act2_uuid,
        },
    }
    actions.append(act2)

    # Action 3: Get Wi-Fi Network Name
    act3_uuid = str(uuid.uuid4()).upper()
    act3 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getwifinetworkname",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Wi-Fi Name",
            "UUID": act3_uuid,
        },
    }
    actions.append(act3)

    # Action 4: Text block: Trigger Details (Sender & Content from Shortcut Input)
    act4_uuid = str(uuid.uuid4()).upper()
    act4 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Trigger Details",
            "UUID": act4_uuid,
            "WFTextActionText": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "Aggrandizements": [
                                {
                                    "PropertyName": "Sender",
                                    "Type": "WFPropertyVariableAggrandizement",
                                }
                            ],
                            "Type": "ExtensionInput",
                        },
                        "{2, 1}": {
                            "Aggrandizements": [
                                {
                                    "PropertyName": "Content",
                                    "Type": "WFPropertyVariableAggrandizement",
                                }
                            ],
                            "Type": "ExtensionInput",
                        },
                    },
                    "string": "\ufffc \ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act4)

    # --- PANIC / EMERGENCY BEACON CHECK ---
    # Action 5: Match PANIC keywords
    act5_uuid = str(uuid.uuid4()).upper()
    act5 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.text.match",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Panic Matches",
            "UUID": act5_uuid,
            "WFMatchTextCaseSensitive": False,
            "WFMatchTextPattern": r"PANIC|CMD_LOCATE|SOS",
            "text": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Trigger Details",
                            "OutputUUID": act4_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act5)

    group_panic = str(uuid.uuid4()).upper()

    # Action 6: If Panic Matches has any value -> Emergency Beacon Flow
    act6 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_panic,
            "WFCondition": 100,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "OutputName": "Panic Matches",
                        "OutputUUID": act5_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act6)

    # Action 7: Get Current Location
    act7_uuid = str(uuid.uuid4()).upper()
    act7 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getcurrentlocation",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Current Location",
            "UUID": act7_uuid,
        },
    }
    actions.append(act7)

    # Action 8: POST /api/v1/relay/panic with GPS and battery
    act8_uuid = str(uuid.uuid4()).upper()
    act8 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Panic Beacon Response",
            "UUID": act8_uuid,
            "WFHTTPMethod": "POST",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "token"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Type": "Variable",
                                            "VariableName": "Tenant Token",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "sender"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Sender",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "Type": "ExtensionInput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "lat"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Latitude",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "OutputName": "Current Location",
                                            "OutputUUID": act7_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "lon"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Longitude",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "OutputName": "Current Location",
                                            "OutputUUID": act7_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "altitude"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Altitude",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "OutputName": "Current Location",
                                            "OutputUUID": act7_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "battery"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Battery Level",
                                            "OutputUUID": act2_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/api/v1/relay/panic",
        },
    }
    actions.append(act8)

    # Action 9: Exit Shortcut immediately after panic beacon dispatch
    act9 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.exit",
        "WFWorkflowActionParameters": {},
    }
    actions.append(act9)

    # Action 10: End If (Panic check)
    act10 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_panic,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act10)

    # --- STANDARD INBOUND / OUTBOUND BRANCHING ---
    # Action 11: Text Match: Match Gateway sender, trigger keywords, or empty manual tap
    act11_uuid = str(uuid.uuid4()).upper()
    act11 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.text.match",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Trigger Matches",
            "UUID": act11_uuid,
            "WFMatchTextCaseSensitive": False,
            "WFMatchTextPattern": r"432172148|SYNC_AI|DOORBELL|^\s*$",
            "text": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Trigger Details",
                            "OutputUUID": act4_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act11)

    group_branch = str(uuid.uuid4()).upper()

    # Action 12: If Trigger Matches has any value -> True Branch (Outbound Mode)
    act12 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_branch,
            "WFCondition": 100,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "OutputName": "Trigger Matches",
                        "OutputUUID": act11_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act12)

    # --- TRUE BRANCH: OUTBOUND MODE ---

    # Action 13: POST /webhook/log (Doorbell wake-up log with battery and wifi telemetry)
    act13_uuid = str(uuid.uuid4()).upper()
    act13 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Wakeup Telemetry Response",
            "UUID": act13_uuid,
            "WFHTTPMethod": "POST",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "event"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {"string": "DOORBELL_WOKE_UP"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "token"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Type": "Variable",
                                            "VariableName": "Tenant Token",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "source"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {"string": "ios_shortcut"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "battery_level"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Battery Level",
                                            "OutputUUID": act2_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "wifi_network"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Wi-Fi Name",
                                            "OutputUUID": act3_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/log",
        },
    }
    actions.append(act13)

    # Action 14: GET /webhook/outbound?token=
    url_prefix = "https://assistant-ui-hub.fly.dev/webhook/outbound?token="
    range_key = f"{{{len(url_prefix)}, 1}}"
    act14_uuid = str(uuid.uuid4()).upper()
    act14 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Outbound Response",
            "ShowHeaders": False,
            "UUID": act14_uuid,
            "WFHTTPMethod": "GET",
            "WFURL": {
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
            },
        },
    }
    actions.append(act14)

    # Action 15: Detect Dictionary from Output of Action 14
    act15_uuid = str(uuid.uuid4()).upper()
    act15 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.detect.dictionary",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Outbound JSON Dictionary",
            "UUID": act15_uuid,
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound Response",
                    "OutputUUID": act14_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act15)

    # Action 16: Get Value for Key "has_message"
    act16_uuid = str(uuid.uuid4()).upper()
    act16 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Has Message",
            "UUID": act16_uuid,
            "WFDictionaryKey": "has_message",
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound JSON Dictionary",
                    "OutputUUID": act15_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act16)

    # Action 17: Conditional: If Has Message is "yes"
    group_has_msg = str(uuid.uuid4()).upper()
    act17 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_has_msg,
            "WFCondition": 4,
            "WFConditionalActionString": "yes",
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "Aggrandizements": [
                            {
                                "CoercionItemClass": "WFStringContentItem",
                                "Type": "WFCoercionVariableAggrandizement",
                            }
                        ],
                        "OutputName": "Has Message",
                        "OutputUUID": act16_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act17)

    # Action 18: Get Value for Key "messages"
    act18_uuid = str(uuid.uuid4()).upper()
    act18 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Message Batch List",
            "UUID": act18_uuid,
            "WFDictionaryKey": "messages",
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound JSON Dictionary",
                    "OutputUUID": act15_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act18)

    # Action 19: Repeat with Each in Message Batch List
    group_repeat = str(uuid.uuid4()).upper()
    act19 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_repeat,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Value": {
                    "OutputName": "Message Batch List",
                    "OutputUUID": act18_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act19)

    # Action 20: Get recipient phone
    act20_uuid = str(uuid.uuid4()).upper()
    act20 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Customer Phone",
            "UUID": act20_uuid,
            "WFDictionaryKey": "recipient",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act20)

    # Action 21: Get message body
    act21_uuid = str(uuid.uuid4()).upper()
    act21 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "AI Reply",
            "UUID": act21_uuid,
            "WFDictionaryKey": "body",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act21)

    # Action 22: Get image_url
    act22_uuid = str(uuid.uuid4()).upper()
    act22 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Image URL",
            "UUID": act22_uuid,
            "WFDictionaryKey": "image_url",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act22)

    # Action 23: Get message_id
    act23_uuid = str(uuid.uuid4()).upper()
    act23 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Message ID",
            "UUID": act23_uuid,
            "WFDictionaryKey": "message_id",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act23)

    # --- NATIVE APPLE CALENDAR DISPATCH ---
    # Action 24: Get calendar_event dictionary from message
    act24_uuid = str(uuid.uuid4()).upper()
    act24 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Calendar Event",
            "UUID": act24_uuid,
            "WFDictionaryKey": "calendar_event",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act24)

    group_cal = str(uuid.uuid4()).upper()

    # Action 25: If Calendar Event has any value
    act25 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_cal,
            "WFCondition": 100,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "OutputName": "Calendar Event",
                        "OutputUUID": act24_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act25)

    # Action 26: Get calendar title
    act26_uuid = str(uuid.uuid4()).upper()
    act26 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Event Title",
            "UUID": act26_uuid,
            "WFDictionaryKey": "title",
            "WFInput": {
                "Value": {
                    "OutputName": "Calendar Event",
                    "OutputUUID": act24_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act26)

    # Action 27: Get calendar start_date
    act27_uuid = str(uuid.uuid4()).upper()
    act27 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Event Start",
            "UUID": act27_uuid,
            "WFDictionaryKey": "start_date",
            "WFInput": {
                "Value": {
                    "OutputName": "Calendar Event",
                    "OutputUUID": act24_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act27)

    # Action 28: Get calendar end_date
    act28_uuid = str(uuid.uuid4()).upper()
    act28 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Event End",
            "UUID": act28_uuid,
            "WFDictionaryKey": "end_date",
            "WFInput": {
                "Value": {
                    "OutputName": "Calendar Event",
                    "OutputUUID": act24_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act28)

    # Action 29: Get calendar location
    act29_uuid = str(uuid.uuid4()).upper()
    act29 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Event Location",
            "UUID": act29_uuid,
            "WFDictionaryKey": "location",
            "WFInput": {
                "Value": {
                    "OutputName": "Calendar Event",
                    "OutputUUID": act24_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act29)

    # Action 30: Get calendar notes
    act30_uuid = str(uuid.uuid4()).upper()
    act30 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Event Notes",
            "UUID": act30_uuid,
            "WFDictionaryKey": "notes",
            "WFInput": {
                "Value": {
                    "OutputName": "Calendar Event",
                    "OutputUUID": act24_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act30)

    # Action 31: Add New Calendar Event (ShowWhenRun: False)
    act31_uuid = str(uuid.uuid4()).upper()
    act31 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.addnewevent",
        "WFWorkflowActionParameters": {
            "ShowWhenRun": False,
            "UUID": act31_uuid,
            "WFCalendarItemTitle": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Event Title",
                            "OutputUUID": act26_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFCalendarItemStartDate": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Event Start",
                            "OutputUUID": act27_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFCalendarItemEndDate": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Event End",
                            "OutputUUID": act28_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFCalendarItemLocation": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Event Location",
                            "OutputUUID": act29_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFCalendarItemNotes": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Event Notes",
                            "OutputUUID": act30_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act31)

    # Action 32: End If (Calendar event check)
    act32 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_cal,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act32)

    # --- MESSAGE DISPATCH (MMS or SMS) ---
    # Action 33: If Image URL has any value (MMS vs SMS)
    group_media = str(uuid.uuid4()).upper()
    act33 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_media,
            "WFCondition": 100,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Type": "Variable",
                "Variable": {
                    "Value": {
                        "Aggrandizements": [
                            {
                                "CoercionItemClass": "WFURLContentItem",
                                "Type": "WFCoercionVariableAggrandizement",
                            }
                        ],
                        "OutputName": "Image URL",
                        "OutputUUID": act22_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act33)

    # Action 34: Download Media URL
    act34_uuid = str(uuid.uuid4()).upper()
    act34 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Downloaded Media",
            "UUID": act34_uuid,
            "WFHTTPMethod": "GET",
            "WFURL": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Image URL",
                            "OutputUUID": act22_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act34)

    # Action 35: Send Message (MMS) with ShowWhenRun: False
    act35_uuid = str(uuid.uuid4()).upper()
    act35 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.sendmessage",
        "WFWorkflowActionParameters": {
            "IntentSlug": "sirikit.intents.custom.SendMessageIntent",
            "ShowWhenRun": False,
            "UUID": act35_uuid,
            "WFSendMessageActionAttachments": {
                "Value": {
                    "OutputName": "Downloaded Media",
                    "OutputUUID": act34_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
            "WFSendMessageActionRecipients": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Customer Phone",
                            "OutputUUID": act20_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFSendMessageActionText": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "AI Reply",
                            "OutputUUID": act21_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act35)

    # Action 36: Conditional: Otherwise (SMS)
    act36 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_media,
            "WFControlFlowMode": 1,
        },
    }
    actions.append(act36)

    # Action 37: Send Message (SMS) with ShowWhenRun: False
    act37_uuid = str(uuid.uuid4()).upper()
    act37 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.sendmessage",
        "WFWorkflowActionParameters": {
            "IntentSlug": "sirikit.intents.custom.SendMessageIntent",
            "ShowWhenRun": False,
            "UUID": act37_uuid,
            "WFSendMessageActionRecipients": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Customer Phone",
                            "OutputUUID": act20_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
            "WFSendMessageActionText": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "AI Reply",
                            "OutputUUID": act21_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act37)

    # Action 38: End If (media)
    act38 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_media,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act38)

    # Action 39: POST /webhook/sent (Sent delivery receipt)
    act39_uuid = str(uuid.uuid4()).upper()
    act39 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Delivery Ack Response",
            "UUID": act39_uuid,
            "WFHTTPMethod": "POST",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "token"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Type": "Variable",
                                            "VariableName": "Tenant Token",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "message_id"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Message ID",
                                            "OutputUUID": act23_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "status"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {"string": "SENT_HANDOFF"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/sent",
        },
    }
    actions.append(act39)

    # Action 40: POST /webhook/log (Sent telemetry with battery and wifi)
    act40_uuid = str(uuid.uuid4()).upper()
    act40 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Telemetry Response",
            "UUID": act40_uuid,
            "WFHTTPMethod": "POST",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "event"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {"string": "SENT_HANDOFF"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "token"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Type": "Variable",
                                            "VariableName": "Tenant Token",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "phone"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Customer Phone",
                                            "OutputUUID": act20_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "message_id"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Message ID",
                                            "OutputUUID": act23_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "battery_level"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Battery Level",
                                            "OutputUUID": act2_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "wifi_network"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Wi-Fi Name",
                                            "OutputUUID": act3_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/log",
        },
    }
    actions.append(act40)

    # Action 41: End Repeat
    act41 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_repeat,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act41)

    # Action 42: End If (Has Message)
    act42 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_has_msg,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act42)

    # --- OTHERWISE BRANCH: INBOUND MODE ---

    # Action 43: Conditional Otherwise (group_branch)
    act43 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_branch,
            "WFControlFlowMode": 1,
        },
    }
    actions.append(act43)

    # Action 44: POST /webhook/inbound
    # Sends: {"token": [Tenant Token], "sender": [Sender], "message": [Content], "source": "ios_shortcut", "battery_level": [Battery], "wifi_network": [Wi-Fi]}
    act44_uuid = str(uuid.uuid4()).upper()
    act44 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Inbound Webhook Response",
            "ShowHeaders": False,
            "UUID": act44_uuid,
            "WFHTTPMethod": "POST",
            "WFJSONValues": {
                "Value": {
                    "WFDictionaryFieldValueItems": [
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "token"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Type": "Variable",
                                            "VariableName": "Tenant Token",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "sender"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Sender",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "Type": "ExtensionInput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "message"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "Aggrandizements": [
                                                {
                                                    "PropertyName": "Content",
                                                    "Type": "WFPropertyVariableAggrandizement",
                                                }
                                            ],
                                            "Type": "ExtensionInput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "source"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {"string": "ios_shortcut"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "battery_level"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Battery Level",
                                            "OutputUUID": act2_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                        {
                            "WFItemType": 0,
                            "WFKey": {
                                "Value": {"string": "wifi_network"},
                                "WFSerializationType": "WFTextTokenString",
                            },
                            "WFValue": {
                                "Value": {
                                    "attachmentsByRange": {
                                        "{0, 1}": {
                                            "OutputName": "Wi-Fi Name",
                                            "OutputUUID": act3_uuid,
                                            "Type": "ActionOutput",
                                        }
                                    },
                                    "string": "\ufffc",
                                },
                                "WFSerializationType": "WFTextTokenString",
                            },
                        },
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/inbound",
        },
    }
    actions.append(act44)

    # Action 45: End If (group_branch)
    act45 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_branch,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act45)

    workflow["WFWorkflowActions"] = actions

    # Write XML plist
    with open(UNIFIED_PLIST_PATH, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_XML)
    print(f"Saved XML plist: {UNIFIED_PLIST_PATH} ({len(actions)} actions)")

    # Write binary shortcut
    with open(UNIFIED_SHORTCUT_PATH, "wb") as f:
        plistlib.dump(workflow, f, fmt=plistlib.FMT_BINARY)
    print(f"Saved binary shortcut: {UNIFIED_SHORTCUT_PATH}")

    # Copy to distribution directories
    dest_dirs = [
        os.path.join(REPO_ROOT, "frontend", "public"),
        os.path.join(REPO_ROOT, "frontend", "dist"),
        os.path.join(REPO_ROOT, "data", "static"),
    ]

    for d in dest_dirs:
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(UNIFIED_PLIST_PATH, os.path.join(d, "AI_Booking_Relay.plist"))
        shutil.copyfile(UNIFIED_SHORTCUT_PATH, os.path.join(d, "AI_Booking_Relay.shortcut"))
        print(f"Copied artifacts to {d}")


if __name__ == "__main__":
    build_unified_relay_workflow()
