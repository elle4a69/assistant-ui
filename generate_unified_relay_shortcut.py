"""
Builder for AI_Booking_Relay.shortcut and AI_Booking_Relay.plist
Complies with Frank's unified iOS Shortcut specification:
1. Single Import Question: "Enter your Booking System Client Token:"
2. Branch on Sender / Trigger:
   - Extract Sender and Content from Shortcut Input
   - If condition matching Central Android Gateway (+61432172148 / 432172148) or SYNC_AI / DOORBELL or empty manual tap:
     - True Branch (Outbound Mode): Doorbell log, GET /webhook/outbound, loop SMS/MMS with ShowWhenRun: False, /webhook/sent receipt
     - Otherwise Branch (Inbound Mode): POST /webhook/inbound sending {token, sender, message, source: "ios_shortcut"}
3. Compatibility: Show When Run: False on Send Message, readable CustomOutputNames.
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
        "WFWorkflowIconGlyphNumber": 59795, # Message icon
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

    # Action 2: Text block: Trigger Details (extract Sender & Content from Shortcut Input)
    act2_uuid = str(uuid.uuid4()).upper()
    act2 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Trigger Details",
            "UUID": act2_uuid,
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
    actions.append(act2)

    # Action 3: Text Match: Match Gateway sender or trigger keywords
    # Matches: Central Gateway phone "432172148", trigger keywords "SYNC_AI", "DOORBELL",
    # or empty string/whitespace (manual tap without shortcut input)
    act3_uuid = str(uuid.uuid4()).upper()
    act3 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.text.match",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Trigger Matches",
            "UUID": act3_uuid,
            "WFMatchTextCaseSensitive": False,
            "WFMatchTextPattern": r"432172148|SYNC_AI|DOORBELL|^\s*$",
            "text": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Trigger Details",
                            "OutputUUID": act2_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act3)

    group_branch = str(uuid.uuid4()).upper()

    # Action 4: If Trigger Matches has any value -> True Branch (Outbound Mode)
    act4 = {
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
                        "OutputUUID": act3_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act4)

    # --- TRUE BRANCH: OUTBOUND MODE ---

    # Action 5: POST /webhook/log (Doorbell wake-up log)
    act5_uuid = str(uuid.uuid4()).upper()
    act5 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Wakeup Telemetry Response",
            "UUID": act5_uuid,
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
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/log",
        },
    }
    actions.append(act5)

    # Action 6: GET /webhook/outbound?token=
    url_prefix = "https://assistant-ui-hub.fly.dev/webhook/outbound?token="
    range_key = f"{{{len(url_prefix)}, 1}}"
    act6_uuid = str(uuid.uuid4()).upper()
    act6 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Outbound Response",
            "ShowHeaders": False,
            "UUID": act6_uuid,
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
    actions.append(act6)

    # Action 7: Detect Dictionary from Output of Action 6
    act7_uuid = str(uuid.uuid4()).upper()
    act7 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.detect.dictionary",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Outbound JSON Dictionary",
            "UUID": act7_uuid,
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound Response",
                    "OutputUUID": act6_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act7)

    # Action 8: Get Value for Key "has_messages"
    act8_uuid = str(uuid.uuid4()).upper()
    act8 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Has Message",
            "UUID": act8_uuid,
            "WFDictionaryKey": "has_messages",
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound JSON Dictionary",
                    "OutputUUID": act7_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act8)

    # Action 9: Conditional: If Has Message is "yes"
    group_has_msg = str(uuid.uuid4()).upper()
    act9 = {
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
                        "OutputUUID": act8_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act9)

    # Action 10: Get Value for Key "messages"
    act10_uuid = str(uuid.uuid4()).upper()
    act10 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Message Batch List",
            "UUID": act10_uuid,
            "WFDictionaryKey": "messages",
            "WFInput": {
                "Value": {
                    "OutputName": "Outbound JSON Dictionary",
                    "OutputUUID": act7_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act10)

    # Action 11: Repeat with Each in Message Batch List
    group_repeat = str(uuid.uuid4()).upper()
    act11 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_repeat,
            "WFControlFlowMode": 0,
            "WFInput": {
                "Value": {
                    "OutputName": "Message Batch List",
                    "OutputUUID": act10_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act11)

    # Action 12: Get recipient phone
    act12_uuid = str(uuid.uuid4()).upper()
    act12 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Customer Phone",
            "UUID": act12_uuid,
            "WFDictionaryKey": "recipient",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act12)

    # Action 13: Get message body
    act13_uuid = str(uuid.uuid4()).upper()
    act13 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "AI Reply",
            "UUID": act13_uuid,
            "WFDictionaryKey": "body",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act13)

    # Action 14: Get image_url
    act14_uuid = str(uuid.uuid4()).upper()
    act14 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Image URL",
            "UUID": act14_uuid,
            "WFDictionaryKey": "image_url",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act14)

    # Action 15: Get message_id
    act15_uuid = str(uuid.uuid4()).upper()
    act15 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Message ID",
            "UUID": act15_uuid,
            "WFDictionaryKey": "message_id",
            "WFInput": {
                "Value": {
                    "Type": "RepeatItem",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
        },
    }
    actions.append(act15)

    # Action 16: If Image URL has any value (MMS vs SMS)
    group_media = str(uuid.uuid4()).upper()
    act16 = {
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
                        "OutputUUID": act14_uuid,
                        "Type": "ActionOutput",
                    },
                    "WFSerializationType": "WFTextTokenAttachment",
                },
            },
        },
    }
    actions.append(act16)

    # Action 17: Download Media URL
    act17_uuid = str(uuid.uuid4()).upper()
    act17 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Downloaded Media",
            "UUID": act17_uuid,
            "WFHTTPMethod": "GET",
            "WFURL": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Image URL",
                            "OutputUUID": act14_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act17)

    # Action 18: Send Message (MMS) with ShowWhenRun: False
    act18_uuid = str(uuid.uuid4()).upper()
    act18 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.sendmessage",
        "WFWorkflowActionParameters": {
            "IntentSlug": "sirikit.intents.custom.SendMessageIntent",
            "ShowWhenRun": False,
            "UUID": act18_uuid,
            "WFSendMessageActionAttachments": {
                "Value": {
                    "OutputName": "Downloaded Media",
                    "OutputUUID": act17_uuid,
                    "Type": "ActionOutput",
                },
                "WFSerializationType": "WFTextTokenAttachment",
            },
            "WFSendMessageActionRecipients": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Customer Phone",
                            "OutputUUID": act12_uuid,
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
                            "OutputUUID": act13_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act18)

    # Action 19: Conditional: Otherwise (SMS)
    act19 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_media,
            "WFControlFlowMode": 1,
        },
    }
    actions.append(act19)

    # Action 20: Send Message (SMS) with ShowWhenRun: False
    act20_uuid = str(uuid.uuid4()).upper()
    act20 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.sendmessage",
        "WFWorkflowActionParameters": {
            "IntentSlug": "sirikit.intents.custom.SendMessageIntent",
            "ShowWhenRun": False,
            "UUID": act20_uuid,
            "WFSendMessageActionRecipients": {
                "Value": {
                    "attachmentsByRange": {
                        "{0, 1}": {
                            "OutputName": "Customer Phone",
                            "OutputUUID": act12_uuid,
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
                            "OutputUUID": act13_uuid,
                            "Type": "ActionOutput",
                        }
                    },
                    "string": "\ufffc",
                },
                "WFSerializationType": "WFTextTokenString",
            },
        },
    }
    actions.append(act20)

    # Action 21: End If (media)
    act21 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_media,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act21)

    # Action 22: POST /webhook/sent (Sent delivery receipt)
    act22_uuid = str(uuid.uuid4()).upper()
    act22 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Delivery Ack Response",
            "UUID": act22_uuid,
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
                                            "OutputUUID": act15_uuid,
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
    actions.append(act22)

    # Action 23: POST /webhook/log (Sent telemetry)
    act23_uuid = str(uuid.uuid4()).upper()
    act23 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Telemetry Response",
            "UUID": act23_uuid,
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
                                            "OutputUUID": act12_uuid,
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
                                            "OutputUUID": act15_uuid,
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
    actions.append(act23)

    # Action 24: End Repeat
    act24 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.repeat.each",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_repeat,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act24)

    # Action 25: End If (Has Message)
    act25 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_has_msg,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act25)

    # --- OTHERWISE BRANCH: INBOUND MODE ---

    # Action 26: Conditional Otherwise
    act26 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_branch,
            "WFControlFlowMode": 1,
        },
    }
    actions.append(act26)

    # Action 27: POST /webhook/inbound
    # Sends: {"token": [Tenant Token], "sender": [Sender], "message": [Content], "source": "ios_shortcut"}
    act27_uuid = str(uuid.uuid4()).upper()
    act27 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
        "WFWorkflowActionParameters": {
            "CustomOutputName": "Inbound Webhook Response",
            "ShowHeaders": False,
            "UUID": act27_uuid,
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
                    ]
                },
                "WFSerializationType": "WFDictionaryFieldValue",
            },
            "WFURL": "https://assistant-ui-hub.fly.dev/webhook/inbound",
        },
    }
    actions.append(act27)

    # Action 28: End If (group_branch)
    act28 = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
        "WFWorkflowActionParameters": {
            "GroupingIdentifier": group_branch,
            "WFControlFlowMode": 2,
        },
    }
    actions.append(act28)

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
