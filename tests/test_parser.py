"""Unit tests for batchexecute RPC payload parsing and turn extraction."""

import json
from pathlib import Path
from precious_gems.parser import (
    extract_latest_timestamp,
    extract_turns_from_response_text,
    parse_rpc_payload,
)


def test_extract_latest_timestamp():
    turns = [
        {
            "user_prompt": {"timestamp": "2026-10-19T10:00:00+00:00"},
            "model_response": {"timestamp": "2026-10-19T10:05:00+00:00"},
        },
        {
            "user_prompt": {"timestamp": "2026-10-19T11:00:00+00:00"},
            "model_response": {"timestamp": "2026-10-19T11:02:00+00:00"},
        },
    ]
    assert extract_latest_timestamp(turns) == "2026-10-19T11:02:00+00:00"
    assert extract_latest_timestamp([]) is None


def test_parse_rpc_payload_turn_structure():
    mock_turn_entry = [
        ["c_testconv", "r_turn1"],
        None,
        [["What is a crystal oscillator?"], 2, None, 1],
        [
            [["rc_cand1", ["A crystal oscillator is an electronic oscillator circuit..."]]],
            None,
            [
                [0, None, None, "rc_cand1", [1790622345, 123000000]],
            ],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "3.8 Flash",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "gemini-3.8-flash",
        ],
        [1790622328, 500000000],
    ]

    payload = [[mock_turn_entry]]
    turns = parse_rpc_payload(payload)

    assert len(turns) == 1
    turn = turns[0]
    assert turn["turn_id"] == "r_turn1"
    assert turn["conversation_id"] == "c_testconv"
    assert turn["user_prompt"]["text"] == "What is a crystal oscillator?"
    assert isinstance(turn["user_prompt"]["timestamp"], str)
    assert "2026-09-28T19:05:28" in turn["user_prompt"]["timestamp"]

    assert (
        "A crystal oscillator is an electronic oscillator circuit" in turn["model_response"]["text"]
    )
    assert turn["model_response"]["model_version"] == "gemini-3.8-flash"
    assert isinstance(turn["model_response"]["timestamp"], str)
    assert len(turn["model_response"]["edit_history"]) == 1
    assert isinstance(turn["model_response"]["edit_history"][0]["timestamp"], str)


def test_parse_rpc_payload_with_attachments():
    mock_attachment_entry = [
        ["c_testconv", "r_turn2"],
        None,
        [
            [
                "Classify image.",
                None,
                None,
                None,
                [
                    [
                        None,
                        None,
                        None,
                        [
                            [
                                None,
                                1,
                                "bandtest.png",
                                "https://lh3.googleusercontent.com/test_url",
                                None,
                                "token",
                                None,
                                None,
                                1,
                                [1791821618, 456627000],
                                None,
                                "image/png",
                                None,
                                None,
                                None,
                                [1920, 1080, 77655],
                            ]
                        ],
                    ]
                ],
            ]
        ],
        [[["rc_cand2", ["This is a test image."]]]],
        [1791821618, 600000000],
    ]
    turns = parse_rpc_payload([[mock_attachment_entry]])
    assert len(turns) == 1
    turn = turns[0]
    assert "attachments" in turn["user_prompt"]
    atts = turn["user_prompt"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "bandtest.png"
    assert atts[0]["url"] == "https://lh3.googleusercontent.com/test_url"
    assert atts[0]["mime_type"] == "image/png"
    assert atts[0]["width"] == 1920
    assert atts[0]["height"] == 1080
    assert atts[0]["size_bytes"] == 77655


def test_parse_rpc_payload_propagated_attachments_not_duplicated():
    # Turn 1: user uploads bandtest.png (present in sub_idx 3 and 4)
    turn1_entry = [
        ["c_testconv", "r_turn1"],
        None,
        [
            [
                "Classify image.",
                None,
                None,
                None,
                [
                    [
                        None,
                        None,
                        None,
                        [
                            [
                                None,
                                1,
                                "bandtest.png",
                                "https://lh3.googleusercontent.com/test_url",
                                None,
                                "tok",
                                None,
                                None,
                                1,
                                [1791821000, 0],
                                None,
                                "image/png",
                            ]
                        ],
                        [
                            [
                                None,
                                1,
                                "bandtest.png",
                                "https://lh3.googleusercontent.com/test_url",
                                None,
                                "tok",
                                None,
                                None,
                                1,
                                [1791821000, 0],
                                None,
                                "image/png",
                            ]
                        ],
                    ]
                ],
            ],
            2,
        ],
        [[["rc_cand1", ["Result."]]]],
        [1791821000, 0],
    ]
    # Turn 2: followup message where Gemini propagates previous attachment in sub_idx 4 only
    turn2_entry = [
        ["c_testconv", "r_turn2"],
        ["c_testconv", "r_turn1", "rc_cand1"],
        [
            [
                "Continue.",
                None,
                None,
                None,
                [
                    [
                        None,
                        None,
                        None,
                        [],
                        [
                            [
                                None,
                                1,
                                "bandtest.png",
                                "https://lh3.googleusercontent.com/test_url",
                                None,
                                "tok",
                                None,
                                None,
                                1,
                                [1791821000, 0],
                                None,
                                "image/png",
                            ]
                        ],
                    ]
                ],
            ],
            2,
        ],
        [[["rc_cand2", ["Continued."]]]],
        [1791821100, 0],
    ]
    turns = parse_rpc_payload([[turn1_entry, turn2_entry]])
    assert len(turns) == 2
    assert "attachments" in turns[0]["user_prompt"]
    assert len(turns[0]["user_prompt"]["attachments"]) == 1
    assert "attachments" not in turns[1]["user_prompt"]


def test_parse_rpc_payload_with_document_attachment():
    download_url = (
        "https://contribution.usercontent.google.com/download?c=test&filename=Example+Domain.pdf"
    )
    mock_doc_entry = [
        ["c_testconv", "r_turn_pdf"],
        None,
        [
            [
                "Read document.",
                None,
                None,
                None,
                [
                    [
                        None,
                        None,
                        None,
                        [
                            [
                                None,
                                11,
                                "Example Domain.pdf",
                                None,
                                None,
                                "token_aes",
                                None,
                                [
                                    "https://drive.google.com/viewer/thumb?ds=thumb",
                                    download_url,
                                    "https://drive.google.com/viewer/upload?ds=upload",
                                ],
                                1,
                                [1791829622, 0],
                                None,
                                "application/pdf",
                            ]
                        ],
                        [],
                    ]
                ],
            ],
            2,
        ],
        [[["rc_cand_doc", ["Read successfully."]]]],
        [1791829622, 0],
    ]
    turns = parse_rpc_payload([[mock_doc_entry]])
    assert len(turns) == 1
    turn = turns[0]
    assert "attachments" in turn["user_prompt"]
    atts = turn["user_prompt"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "Example Domain.pdf"
    assert atts[0]["url"] == download_url
    assert atts[0]["mime_type"] == "application/pdf"


def test_extract_turns_from_response_text():
    inner_payload = [
        [
            [
                ["c_test1", "r_turnA"],
                None,
                [["User query 1"], 2],
                [[["rc_1", ["Model answer 1"]]]],
                [1790000000, 0],
            ]
        ]
    ]
    envelope = [["wrb.fr", "hNvQHb", json.dumps(inner_payload), None, "generic"]]
    response_text = f")]}}'\n\n123\n{json.dumps(envelope)}\n"

    turns = extract_turns_from_response_text(response_text)
    assert len(turns) == 1
    assert turns[0]["turn_id"] == "r_turnA"
    assert turns[0]["user_prompt"]["text"] == "User query 1"
    assert turns[0]["model_response"]["text"] == "Model answer 1"


def test_parse_rpc_payload_with_search_and_grounding():
    mock_turn_entry = [
        ["c_testconv", "r_turn3"],
        ["c_testconv", "r_turn2", "rc_prev"],
        [["Find printer specs."], 2],
        [
            [
                [
                    "rc_cand3",
                    ["Here are the specs."],
                    [
                        None,
                        [
                            [
                                ["Printer model 940c", None, None, [[10, 28], [10, 28]]],
                                [1],
                                [
                                    [
                                        "https://example.com/spec",
                                        "Printer Spec",
                                        None,
                                        "Snippet text",
                                    ]
                                ],
                                "spp_123",
                            ]
                        ],
                    ],
                ]
            ],
            [["printer model 940c specs", 1], ["printer driver linux", 1]],
        ],
        [1790001000, 0],
    ]
    turns = parse_rpc_payload([[mock_turn_entry]])
    assert len(turns) == 1
    turn = turns[0]
    assert turn["parent_turn_id"] == "r_turn2"
    assert turn["model_response"]["search_queries"] == [
        "printer model 940c specs",
        "printer driver linux",
    ]
    grounding = turn["model_response"]["web_search_grounding"]
    assert len(grounding) == 1
    assert grounding[0]["phrase"] == "Printer model 940c"
    assert grounding[0]["indices"] == [[10, 28], [10, 28]]
    assert len(grounding[0]["sources"]) == 1
    assert grounding[0]["sources"][0]["url"] == "https://example.com/spec"
    assert grounding[0]["sources"][0]["title"] == "Printer Spec"
    assert grounding[0]["sources"][0]["snippet"] == "Snippet text"


def test_parse_rpc_payload_with_custom_instructions():
    mock_turn_entry = [
        ["c_testconv", "r_turn4"],
        None,
        [["Hello bot."], 2],
        [[["rc_cand4", ["Hello user."]]]],
        [1790002000, 0],
        None,
        None,
        "bot_id_12345",
        None,
        ["Robot Assistant", "A helpful technical assistant.", "System prompt: zero fluff."],
        [
            None,
            ["Robot Assistant", "A helpful technical assistant."],
            ["System prompt: zero fluff."],
        ],
    ]
    turns = parse_rpc_payload([[mock_turn_entry]])
    assert len(turns) == 1
    turn = turns[0]
    assert "custom_instructions" in turn
    assert turn["custom_instructions"]["id"] == "bot_id_12345"
    assert turn["custom_instructions"]["name"] == "Robot Assistant"
    assert turn["custom_instructions"]["description"] == "A helpful technical assistant."
    assert turn["custom_instructions"]["instructions"] == "System prompt: zero fluff."


def test_parse_rpc_payload_with_widgets_images_and_video():
    # Construct a turn with cand[41] widget, cand[12][1] carousel image, cand[12][2]['60'] video
    cand = [
        "rc_cand1",
        ["Here are the widgets and media."],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        # cand[12]: [None, images_list, dict_with_video]
        [
            None,
            [
                [
                    [["https://example.com/image1.jpg"]],
                    [["https://gettyimages.com/image1"]],
                    1920,
                    1080,
                    None,
                    None,
                    None,
                    ["http://googleusercontent.com/image_agent_tag_123", None, "Lunar Rover"],
                ]
            ],
            {
                "60": [
                    [
                        [
                            [
                                None,
                                2,
                                "",
                                None,
                                None,
                                "$signature",
                                None,
                                ["https://contribution.usercontent.google.com/download?video1"],
                                None,
                                None,
                                None,
                                "video/mp4",
                                None,
                                None,
                                None,
                                None,
                                None,
                                [[52, 0], 1280, 720],
                            ],
                            None,
                            ["http://googleusercontent.com/video_generation_content/vid_456"],
                        ]
                    ]
                ]
            },
        ],
    ]
    # Pad cand to length > 41
    while len(cand) < 41:
        cand.append(None)
    # cand[41]: widgets list
    cand.append(
        [
            [
                "DeepzoomWidget_1",
                "700px",
                None,
                None,
                "```html\n<!DOCTYPE html><html><body>Deepzoom Content</body></html>\n```",
                None,
                None,
                None,
                None,
                "Interactive Explorer",
            ]
        ]
    )

    cand_slot = [cand]
    entry3 = [cand_slot, None, [[0, None, None, "rc_cand1", [1790622345, 0]]]]
    # Pad entry3 to have version string
    while len(entry3) < 32:
        entry3.append(None)
    entry3[31] = "gemini-4-argon"

    mock_entry = [
        ["c_testconv", "r_turn3"],
        None,
        [["Show widgets."], 2, None, 1],
        entry3,
        [1790622328, 0],
    ]

    payload = [[mock_entry]]
    turns = parse_rpc_payload(payload)

    assert len(turns) == 1
    model_resp = turns[0]["model_response"]
    assert "attachments" in model_resp
    atts = model_resp["attachments"]
    assert len(atts) == 3

    # Widget attachment
    w_att = next(a for a in atts if a["mime_type"] == "text/html")
    assert w_att["filename"] == "DeepzoomWidget_1.html"
    assert w_att["title"] == "Interactive Explorer"
    assert "<!DOCTYPE html><html><body>Deepzoom Content</body></html>" in w_att["html_content"]
    assert not w_att["html_content"].startswith("```")

    # Image attachment
    img_att = next(a for a in atts if a["mime_type"] == "image/jpeg")
    assert img_att["filename"] == "image_agent_tag_123.jpg"
    assert img_att["url"] == "https://example.com/image1.jpg"
    assert img_att["title"] == "Lunar Rover"
    assert img_att["width"] == 1920

    # Video attachment
    vid_att = next(a for a in atts if a["mime_type"] == "video/mp4")
    assert vid_att["filename"] == "vid_456.mp4"
    assert "download?video1" in vid_att["url"]
    assert vid_att["width"] == 1280
    assert vid_att["height"] == 720


def test_parse_rpc_payload_with_generated_images_and_thought_signatures():
    gen_img_entry = [
        # [0]: img_info nested
        [
            [
                [
                    [
                        None,
                        None,
                        None,
                        [
                            None,
                            1,
                            "watermarked_img_12345.jpg",
                            "https://lh3.googleusercontent.com/gg/mock_image_url",
                            None,
                            None,
                            None,
                            None,
                            None,
                            [1792408174, 0],
                            None,
                            "image/jpeg",
                            None,
                            None,
                            None,
                            [1024, 1024, 54321],
                        ],
                    ]
                ]
            ]
        ],
        # [1]: placeholder url
        ["http://googleusercontent.com/image_generation_content/0_999"],
        None,
        # [3]: metadata
        [
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "imagen_default.ultra",  # [18] model_name
            None,
            None,
            None,
            [
                None,
                3,
                "thought_signature_12345.pb",  # [2]
                None,
                None,
                None,
                None,
                ["", "https://contribution.usercontent.google.com/download?thought1", ""],  # [7]
                None,
                [1792408174, 0],
                None,
                "application/protobuf",  # [11]
            ],  # [22] thought signature
        ],
        None,
        None,
        None,
        None,
        "im_mock_banana_id",  # [8] image_id
    ]

    cand12 = [None, None, None, None, None, None, None, [[gen_img_entry]]]
    cand = [
        "rc_test_1",
        [
            "\nHere is your image:\nhttp://googleusercontent.com/image_generation_content/0_999\nEnjoy!"
        ],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        cand12,
    ]

    entry3 = [[cand]]
    mock_entry = [
        "c_123",
        "r_123",
        [None, [["Generate a banana"]], None, []],
        entry3,
        [1792408174, 0],
    ]

    payload = [[mock_entry]]
    turns = parse_rpc_payload(payload)

    assert len(turns) == 1
    model_resp = turns[0]["model_response"]

    # Verify placeholder URL was replaced in text
    assert "http://googleusercontent.com/image_generation_content/0_999" not in model_resp["text"]
    assert "watermarked_img_12345.jpg" in model_resp["text"]

    # Verify attachments
    assert "attachments" in model_resp
    atts = model_resp["attachments"]
    assert len(atts) == 2

    img_att = next(a for a in atts if a["mime_type"] == "image/jpeg")
    assert img_att["filename"] == "watermarked_img_12345.jpg"
    assert img_att["url"] == "https://lh3.googleusercontent.com/gg/mock_image_url"
    assert img_att["width"] == 1024
    assert img_att["height"] == 1024
    assert img_att["size_bytes"] == 54321
    assert img_att["image_id"] == "im_mock_banana_id"
    assert img_att["model_name"] == "imagen_default.ultra"
    assert "placeholder_url" not in img_att

    th_att = next(a for a in atts if a["mime_type"] == "application/protobuf")
    assert th_att["filename"] == "thought_signature_12345.pb"
    assert th_att["url"] == "https://contribution.usercontent.google.com/download?thought1"
    assert th_att["image_id"] == "im_mock_banana_id"
    assert th_att["model_name"] == "imagen_default.ultra"
