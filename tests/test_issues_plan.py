"""The dependency rule: which issues may start from a clean conversation.

This is the one piece of judgement issue-pilot takes away from the model, so it
is the piece that has to be pinned down by tests.
"""
import json
import unittest

from support import PilotTestCase, issue


class DependencyRule(PilotTestCase):
    def plan(self, issues, order):
        self.write_issues({i["number"]: i for i in issues})
        out = self.run_script("issues_plan.py", *[str(n) for n in order])
        return {p["issue"]: p for p in json.loads(out.stdout)["plan"]}

    def test_independent_issues_all_start_clean(self):
        plan = self.plan([issue(1), issue(2), issue(3)], [1, 2, 3])
        for n in (1, 2, 3):
            self.assertTrue(plan[n]["clear_before"], n)
            self.assertEqual(plan[n]["depends_on"], [])

    def test_later_issue_naming_an_earlier_one_shares_its_conversation(self):
        plan = self.plan([issue(1), issue(2, body="builds on #1")], [1, 2])
        self.assertEqual(plan[2]["depends_on"], [1])
        self.assertFalse(plan[2]["clear_before"])

    def test_the_rule_is_symmetric(self):
        # #1 naming #2 means the two were written as one piece of work just as
        # much as #2 naming #1 does.
        plan = self.plan([issue(1, body="superseded by #2"), issue(2)], [1, 2])
        self.assertEqual(plan[2]["depends_on"], [1])

    def test_a_mention_in_a_comment_counts(self):
        plan = self.plan([issue(1), issue(2, comments=["actually this needs #1"])],
                         [1, 2])
        self.assertEqual(plan[2]["depends_on"], [1])

    def test_full_issue_urls_count_too(self):
        body = "see https://github.com/INGEOTEC/issue-pilot/issues/1 for context"
        plan = self.plan([issue(1), issue(2, body=body)], [1, 2])
        self.assertEqual(plan[2]["depends_on"], [1])

    def test_mentions_outside_the_run_are_ignored(self):
        # #99 is not part of this run, so it cannot be a dependency of it.
        plan = self.plan([issue(1), issue(2, body="related to #99")], [1, 2])
        self.assertEqual(plan[2]["depends_on"], [])
        self.assertTrue(plan[2]["clear_before"])

    def test_an_issue_does_not_depend_on_itself(self):
        plan = self.plan([issue(1, body="this is #1")], [1])
        self.assertEqual(plan[1]["depends_on"], [])

    def test_dependencies_only_look_backwards(self):
        # #1 mentions #2, but #1 runs first: there is no history to keep yet.
        plan = self.plan([issue(1, body="see #2"), issue(2)], [1, 2])
        self.assertEqual(plan[1]["depends_on"], [])
        self.assertTrue(plan[1]["clear_before"])
        self.assertEqual(plan[2]["depends_on"], [1])

    def test_order_given_is_the_order_planned(self):
        plan = self.plan([issue(1), issue(2), issue(3)], [3, 1, 2])
        self.assertEqual([p["issue"] for p in
                          sorted(plan.values(), key=lambda p: p["issue"])],
                         [1, 2, 3])
        out = self.run_script("issues_plan.py", "3", "1", "2")
        self.assertEqual(json.loads(out.stdout)["issues"], [3, 1, 2])

    def test_a_repeated_issue_is_rejected(self):
        self.write_issues({1: issue(1)})
        out = self.run_script("issues_plan.py", "1", "1", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("more than once", out.stderr)

    def test_an_unknown_issue_fails_loudly(self):
        self.write_issues({1: issue(1)})
        out = self.run_script("issues_plan.py", "1", "42", check=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("42", out.stderr)


if __name__ == "__main__":
    unittest.main()
