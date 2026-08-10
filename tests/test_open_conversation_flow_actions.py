import asyncio
import unittest

from tests.run_open_conversation_selection_cases import (
    _OpenConversationSelectionEngine,
    infer_actual_action,
)


def _run(coro):
    return asyncio.run(coro)


class OpenConversationFlowActionTests(unittest.TestCase):
    def test_post_sector_add_hazard_text_enters_hazard_creation_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Post-sector",
                    "Initial State": (
                        "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
                    ),
                    "User Message": "None of these hazards fit. I want to add one.",
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazards")
        self.assertEqual(session.phase, "custom_hazard_input")
        self.assertEqual(infer_actual_action(response, session), "ADD_NEW_HAZARD")

    def test_post_sector_start_mitigation_text_enters_mitigation_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Post-sector",
                    "Initial State": (
                        "Country=Germany; Region=Baden-Württemberg; Sector=Energy"
                    ),
                    "User Message": "Start mitigation planning",
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "hazard_profile_selection")
        self.assertEqual(session.phase, "hazard_profile_selection")
        self.assertEqual(
            infer_actual_action(response, session),
            "START_MITIGATION_PLANNING",
        )

    def test_reason_confirmation_add_mitigation_text_enters_measure_flow(self):
        engine = _OpenConversationSelectionEngine()

        response, session = _run(
            engine.handle_case(
                {
                    "Step / Current Phase": "Reason confirmation",
                    "Initial State": (
                        "Country=Germany; Region=Bavaria; Sector=Energy; "
                        "Hazard=Heat stress"
                    ),
                    "User Message": (
                        "None of these mitigation measures fit. I want to add one."
                    ),
                }
            )
        )

        self.assertFalse(response.error)
        self.assertEqual(response.step, "mitigation_measure")
        self.assertEqual(session.phase, "mitigation_measure")
        self.assertEqual(
            infer_actual_action(response, session),
            "WRITE_MITIGATION_MANUALLY",
        )


if __name__ == "__main__":
    unittest.main()
