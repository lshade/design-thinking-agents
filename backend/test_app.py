import os
import unittest

from backend import app


class BackendUnitTests(unittest.TestCase):
    def test_sanitize_sticky_text_keeps_atomic_notes(self):
        text = app.sanitize_sticky_text(
            "Parents need allergy-safe meal planning that reduces stress and prevents accidental exposure."
        )

        self.assertLessEqual(len(text.split()), 8)
        self.assertEqual(text, "Parents need allergy-safe meal planning that reduces stress")

    def test_parse_model_json_accepts_fenced_json_and_python_dicts(self):
        self.assertEqual(app.parse_model_json('```json\n{"decision": "approve"}\n```')["decision"], "approve")
        self.assertEqual(app.parse_model_json("{'decision': 'approve'}")["decision"], "approve")

    def test_create_run_initializes_traceable_state(self):
        record = app.create_run("Test idea")

        self.assertEqual(record.status, "running")
        self.assertEqual(record.idea, "Test idea")
        self.assertTrue(record.id)
        self.assertTrue(record.traceId)

    def test_model_routing_uses_specific_env_over_default(self):
        old_default = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME")
        old_worker = os.environ.get("FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME")
        try:
            os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"] = "default-model"
            os.environ["FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME"] = "worker-model"
            orchestrator = app.MafWorkshopOrchestrator()

            self.assertEqual(orchestrator.default_model, "default-model")
            self.assertEqual(orchestrator.worker_model, "worker-model")
        finally:
            if old_default is None:
                os.environ.pop("FOUNDRY_MODEL_DEPLOYMENT_NAME", None)
            else:
                os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"] = old_default
            if old_worker is None:
                os.environ.pop("FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME", None)
            else:
                os.environ["FOUNDRY_WORKER_MODEL_DEPLOYMENT_NAME"] = old_worker

    def test_final_handoff_sections_are_enforced(self):
        brief = app.ensure_final_handoff_sections(
            "# Design Thinking Consensus Brief\n\n## Refined concept\nTest",
            "A test product idea",
            ["Empathize: User pain"],
            [app.ApprovalOutput(speech="Approve", decision="approve", rationale="Clear enough")],
        )

        self.assertIn("## Target users", brief)
        self.assertIn("## Observed evidence", brief)
        self.assertIn("## Problem hypothesis", brief)
        self.assertIn("## Selected concept for prototyping", brief)
        self.assertIn("## MVP boundary", brief)
        self.assertIn("## Working hypotheses", brief)
        self.assertIn("## Assumptions to validate", brief)
        self.assertIn("## Next prototype and validation", brief)
        self.assertIn("## Implementation handoff for coding agents", brief)
        self.assertIn("## Suggested epics and user stories", brief)
        self.assertIn("## Architecture notes", brief)
        self.assertIn("## Acceptance criteria", brief)
        self.assertIn("## Applied delivery transition: Build-readiness gate", brief)
        self.assertIn("not a canonical design-thinking phase", brief)
        self.assertIn("Readiness status:", brief)
        self.assertIn("Blockers:", brief)
        self.assertIn("Required follow-up:", brief)

    def test_blackboard_propagates_into_orchestration_prompts(self):
        blackboard = app.BlackboardState(
            assumptions=["Nurses need shift-aware triage"],
            evidence=["ER intake spikes after 5 PM"],
            concepts=["Shift handoff triage assistant"],
            objections=["Privacy review needs consent language"],
            decisions=["Prototype a handoff summary first"],
            selectedConcept="Shift handoff triage assistant",
        )
        agent = next(agent for agent in app.AGENTS if agent.id == "researcher")
        messages = [
            app.AgentMessage(
                phase=0,
                agentId="researcher",
                text="Interview nurses about ER handoff delays.",
            )
        ]
        critiques = [
            app.CritiqueOutput(
                speech="Consent risk needs details.",
                issue="Consent path is unclear",
                recommendation="Add opt-in language",
            )
        ]
        debate = app.DebateOutput(
            speech="Accept consent objection.",
            responses=[
                app.DebateResponse(
                    objection="Consent path is unclear",
                    disposition="accept",
                    response="Consent must be visible during handoff.",
                    action="Add opt-in handoff notice",
                )
            ],
        )
        approval = app.ApprovalOutput(
            speech="Approve with consent follow-up.",
            decision="approve",
            rationale="Consent follow-up is owned.",
        )

        prompts = [
            app.build_prompt(agent, "ER handoff assistant", 0, messages, blackboard),
            app.build_critique_prompt(agent, "ER handoff assistant", 0, messages, [], blackboard),
            app.build_debate_prompt(agent, "ER handoff assistant", 0, messages, critiques, [], blackboard),
            app.build_revision_prompt(agent, "ER handoff assistant", 0, messages, critiques, debate, [], blackboard),
            app.build_synthesis_prompt("ER handoff assistant", 0, messages, critiques, debate, messages[0], [], blackboard),
            app.build_approval_prompt(agent, "ER handoff assistant", ["Empathize: Handoff delays"], messages, blackboard),
            app.build_final_brief_prompt(
                "ER handoff assistant",
                ["Empathize: Handoff delays"],
                [approval],
                messages,
                blackboard,
            ),
        ]

        for prompt in prompts:
            self.assertIn("Structured swarm blackboard", prompt)
            self.assertIn("Nurses need shift-aware triage", prompt)
            self.assertIn("Prototype a handoff summary first", prompt)
            self.assertIn("Evidence boundary", prompt)
            self.assertIn("Shift handoff triage assistant", prompt)

        self.assertIn("Concrete objections to answer", prompts[2])
        self.assertIn("disposition", prompts[2])
        self.assertIn("## Target users", prompts[-1])
        self.assertIn("## Observed evidence", prompts[-1])
        self.assertIn("## Problem hypothesis", prompts[-1])
        self.assertIn("## Selected concept for prototyping", prompts[-1])
        self.assertIn("## MVP boundary", prompts[-1])
        self.assertIn("## Working hypotheses", prompts[-1])
        self.assertIn("## Assumptions to validate", prompts[-1])
        self.assertIn("## Next prototype and validation", prompts[-1])
        self.assertIn("Build-readiness gate", prompts[-1])

    def test_ideation_lead_prompt_requires_divergent_directions(self):
        blackboard = app.create_blackboard("neighborhood meal swap")
        ideation = next(agent for agent in app.AGENTS if agent.id == "ideation")
        researcher = next(agent for agent in app.AGENTS if agent.id == "researcher")

        ideation_prompt = app.build_prompt(ideation, "neighborhood meal swap", 2, [], blackboard)
        researcher_prompt = app.build_prompt(researcher, "neighborhood meal swap", 0, [], blackboard)

        self.assertIn("Create 3 to 4 Feature stickies", ideation_prompt)
        self.assertIn("Practical", ideation_prompt)
        self.assertIn("Bold", ideation_prompt)
        self.assertIn("Low tech or Manual", ideation_prompt)
        self.assertIn("Create exactly 1 sticky", researcher_prompt)

    def test_ideation_output_enforces_practical_bold_and_low_tech_stickies(self):
        output = app.ensure_agent_output(
            "ideation",
            "neighborhood meal swap",
            2,
            app.AgentOutput(
                speech="Explore several meal swap concepts.",
                stickies=[
                    {"kind": "Feature", "text": "matching app for cooks", "size": "standard"},
                ],
            ),
        )

        sticky_text = " ".join(sticky["text"].lower() for sticky in output.stickies)
        self.assertEqual(len(output.stickies), 3)
        self.assertIn("practical", sticky_text)
        self.assertIn("bold", sticky_text)
        self.assertTrue("low tech" in sticky_text or "manual" in sticky_text)
        self.assertEqual(app.max_stickies_for_output("ideation", 2), 4)
        self.assertEqual(app.max_stickies_for_output("business", 2), 1)

    def test_blackboard_updates_after_phase_with_debate_and_decision(self):
        blackboard = app.create_blackboard("ER handoff assistant")
        contribution = app.AgentMessage(
            phase=0,
            agentId="researcher",
            text="Nurses lose context when ER shift handoffs are rushed.",
        )
        sticky = app.Sticky(
            id="researcher-0-1",
            phase=0,
            agentId="researcher",
            kind="Question",
            text="Which handoff context is missing",
            x=5,
            y=10,
        )
        critique = app.CritiqueOutput(
            speech="Missing consent plan.",
            issue="Consent path is unclear",
            recommendation="Add opt-in language",
        )
        debate = app.DebateOutput(
            speech="Accept consent issue.",
            responses=[
                app.DebateResponse(
                    objection="Consent path is unclear",
                    disposition="accept",
                    response="Consent belongs in the first handoff screen.",
                    action="Add opt-in handoff notice",
                )
            ],
        )
        revision = app.AgentMessage(
            phase=0,
            agentId="researcher",
            text="Revision: test consent and context capture together.",
            kind="revision",
        )

        app.update_blackboard_after_phase(
            blackboard,
            "Empathize",
            [contribution],
            [sticky],
            [critique],
            debate,
            revision,
            "Validate handoff context gaps",
        )

        self.assertTrue(any("Nurses lose context" in item for item in blackboard.assumptions))
        self.assertTrue(any("Which handoff context" in item for item in blackboard.assumptions))
        self.assertTrue(any("Consent path is unclear" in item for item in blackboard.objections))
        self.assertTrue(any("Add opt-in handoff notice" in item for item in blackboard.decisions))
        self.assertTrue(any("Validate handoff context gaps" in item for item in blackboard.decisions))

    def test_selected_concept_updates_blackboard_and_final_fallbacks(self):
        blackboard = app.BlackboardState(
            assumptions=["Care teams will adopt handoff prompts"],
            evidence=["Nurses report lost context during handoff"],
            concepts=["Ideate: Practical handoff checklist"],
            selectedConcept="Practical handoff checklist",
        )
        brief = app.ensure_final_handoff_sections(
            "# Design Thinking Consensus Brief\n\n## Refined concept\nHandoff assistant",
            "Handoff assistant",
            ["Ideate: Practical handoff checklist"],
            [],
            blackboard,
        )

        self.assertIn("## Selected concept for prototyping", brief)
        self.assertIn("Practical handoff checklist", brief)
        self.assertIn("## Observed evidence", brief)
        self.assertIn("Nurses report lost context", brief)

    def test_record_event_sequences_preserve_replayable_state(self):
        record = app.create_run("Replayable idea")
        blackboard = app.BlackboardState(selectedConcept="Selected concept")
        first = app.record_event(record, app.WorkshopEvent(type="blackboard", blackboard=blackboard))
        second = app.record_event(record, app.WorkshopEvent(type="brief", markdown="Final brief"))

        self.assertEqual(first.meta["sequence"], 1)
        self.assertEqual(second.meta["sequence"], 2)
        self.assertEqual(record.latestBlackboard.selectedConcept, "Selected concept")
        self.assertEqual(record.finalBrief, "Final brief")


if __name__ == "__main__":
    unittest.main()
