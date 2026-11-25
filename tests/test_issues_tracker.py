"""One interface for every issue operation, over two trackers.

The local backend is a format on disk, so most of these tests pin the format:
what `create` writes, what a hand-written file is read as, and that rewriting a
file (a comment, a close) changes only what it must.  The github backend is
checked through the fake `gh`, which records what it was asked to do.
"""
import json
import pathlib
import shutil
import stat
import unittest

from support import PilotTestCase, issue


class Local(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true", "tracker": "local"})
        self.dir = self.repo / "issues"

    def tracker(self, *args, check=True, env=None):
        return self.run_script("issues_tracker.py", *args, check=check, env=env)

    def create(self, title="First issue", body="The body.\n", labels=()):
        body_file = self.repo.parent / "body.md"
        body_file.write_text(body)
        args = ["create", "--title", title, "--body-file", str(body_file)]
        for label in labels:
            args += ["--label", label]
        number, location = self.tracker(*args).stdout.splitlines()
        return int(number), location

    def write_issue(self, name, text):
        self.dir.mkdir(exist_ok=True)
        (self.dir / name).write_text(text)

    def comment(self, number, text):
        body_file = self.repo.parent / "comment.md"
        body_file.write_text(text)
        return self.tracker("comment", str(number), "--body-file", str(body_file))

    def fetch(self, number):
        return json.loads(self.tracker("view", str(number), "--json").stdout)

    # -- create and the format ------------------------------------------------

    def test_create_writes_the_file_in_the_documented_format(self):
        number, location = self.create("Add a --format flag", "Body line.\n",
                                       labels=["bug", "export"])
        self.assertEqual((number, location), (1, "issues/0001-add-a-format-flag.md"))
        text = (self.repo / location).read_text()
        header, _, rest = text.partition("\n---\n")
        self.assertTrue(header.startswith("---\nnumber: 1\ntitle: Add a --format flag\nstate: open\n"))
        self.assertRegex(header, r"created: \d{4}-\d\d-\d\d\nclosed:\nlabels: bug, export$")
        self.assertEqual(rest, "\nBody line.\n")

    def test_a_second_create_makes_issue_two(self):
        self.create("One")
        number, location = self.create("Two")
        self.assertEqual((number, location), (2, "issues/0002-two.md"))

    def test_the_number_is_one_more_than_the_largest_not_the_count(self):
        self.write_issue("0007-old.md", "# Old\n")
        self.assertEqual(self.create("New")[0], 8)

    def test_the_directory_is_created_on_first_use(self):
        self.assertFalse(self.dir.exists())
        self.create()
        self.assertTrue(self.dir.is_dir())

    def test_the_slug_is_lowercase_ascii_with_single_dashes(self):
        _, location = self.create("  Fix: the  Über--thing (v2)!  ")
        self.assertEqual(location, "issues/0001-fix-the-ber-thing-v2.md")

    def test_a_title_with_no_ascii_letters_gets_the_slug_issue(self):
        _, location = self.create("日本語")
        self.assertEqual(location, "issues/0001-issue.md")

    def test_the_slug_is_cut_to_fifty_characters(self):
        _, location = self.create("a" * 80)
        self.assertEqual(location, "issues/0001-%s.md" % ("a" * 50))

    def test_a_multiline_title_becomes_one_line(self):
        number, _ = self.create("first\nsecond")
        self.assertEqual(self.fetch(number)["title"], "first second")

    def test_numbers_above_9999_are_simply_longer(self):
        self.write_issue("9999-x.md", "# x\n")
        self.assertEqual(self.create("Next")[1], "issues/10000-next.md")

    def test_issues_dir_is_configurable(self):
        self.write_config({"test_command": "true", "tracker": "local", "issues_dir": "docs/tickets"})
        _, location = self.create("Here")
        self.assertEqual(location, "docs/tickets/0001-here.md")

    # -- view ----------------------------------------------------------------

    def test_view_json_is_the_fetch_object(self):
        self.create("Title", "Body text.\n")
        self.assertEqual(self.fetch(1), {"number": 1, "title": "Title", "body": "Body text.",
                                         "state": "open", "comments": []})

    def test_view_is_readable_and_shows_the_comments(self):
        self.create("Title", "Body text.\n")
        self.comment(1, "First.\n")
        self.comment(1, "Second.\n")
        self.assertEqual(self.tracker("view", "1").stdout,
                         "#1 Title\nstate: open\n\nBody text.\n\n--- comment 1 ---\nFirst.\n"
                         "\n--- comment 2 ---\nSecond.\n")

    def test_viewing_an_issue_that_does_not_exist_names_the_directory(self):
        self.create()
        out = self.tracker("view", "5", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("#5", out.stderr)
        self.assertIn("issues/", out.stderr)

    def test_viewing_with_no_directory_says_so(self):
        out = self.tracker("view", "1", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("issues/", out.stderr)

    def test_zero_padding_does_not_matter_when_finding_an_issue(self):
        self.write_issue("7-x.md", "# Seven\n")
        self.write_issue("0008-y.md", "# Eight\n")
        self.write_issue("009-z.md", "# Nine\n")
        self.assertEqual([self.fetch(n)["title"] for n in (7, 8, 9)], ["Seven", "Eight", "Nine"])

    def test_two_files_for_one_number_are_an_error_naming_both(self):
        self.write_issue("0007-a.md", "# A\n")
        self.write_issue("7-b.md", "# B\n")
        out = self.tracker("view", "7", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("0007-a.md", out.stderr)
        self.assertIn("7-b.md", out.stderr)

    def test_files_that_are_not_issues_are_ignored(self):
        self.write_issue("README.md", "# Notes\n")
        self.write_issue("0001-a.txt", "x")
        self.write_issue("0002-b.md", "# B\n")
        self.assertEqual([i.split("\t")[0] for i in self.tracker("list", "--state", "all").stdout.splitlines()],
                         ["2"])

    # -- hand-written files --------------------------------------------------

    def test_a_hand_written_file_without_a_header_is_open_with_its_heading_as_title(self):
        self.write_issue("12-foo.md", "Some intro.\n\n# The real title\n\nDetails.\n")
        self.assertEqual(self.fetch(12), {
            "number": 12, "title": "The real title", "state": "open", "comments": [],
            "body": "Some intro.\n\n# The real title\n\nDetails."})

    def test_a_hand_written_file_without_a_heading_takes_the_title_from_its_name(self):
        self.write_issue("0003-fix-the-thing.md", "Just text.\n")
        self.assertEqual(self.fetch(3)["title"], "fix-the-thing")

    def test_a_header_that_does_not_close_is_not_a_header(self):
        self.write_issue("0001-x.md", "---\ntitle: nope\n# Heading\n")
        self.assertEqual(self.fetch(1)["title"], "Heading")

    def test_a_horizontal_rule_is_not_a_header(self):
        # Corrects a review finding (fix-1, found after the fact): any text
        # between two `---` lines used to be taken for a header and lost.
        original = "---\n\n# My title\n\ntext\n\n---\n\nmore text\n"
        self.write_issue("5-hand.md", original)
        fetched = self.fetch(5)
        self.assertEqual((fetched["title"], fetched["state"]), ("My title", "open"))
        self.assertIn("text", fetched["body"])
        self.assertIn("more text", fetched["body"])
        self.tracker("close", "5")
        text = (self.dir / "5-hand.md").read_text()
        self.assertTrue(text.endswith("\n---\n\n" + original))
        self.assertRegex(text, r"^---\nnumber: 5\ntitle: My title\nstate: closed\nclosed: ")
        self.assertEqual(self.fetch(5)["state"], "closed")

    def test_a_real_header_with_blank_lines_and_crlf_is_still_one(self):
        self.write_issue("6-x.md", "---\r\ntitle: Windows\r\n\r\nstate: open\r\n---\r\nbody\r\n")
        self.assertEqual(self.fetch(6)["title"], "Windows")

    # -- comments ------------------------------------------------------------

    def test_a_comment_is_appended_and_the_body_is_left_alone(self):
        self.create("Title", "Body.\n\nSecond paragraph.\n")
        path = self.dir / "0001-title.md"
        before = path.read_text()
        out = self.comment(1, "A comment.\n")
        self.assertEqual(out.stdout.strip(), "commented on #1")
        after = path.read_text()
        self.assertTrue(after.startswith(before))
        self.assertRegex(after[len(before):],
                         r"^\n<!-- issue-pilot:comment \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ -->\n\nA comment\.\n$")
        issue_ = self.fetch(1)
        self.assertEqual(issue_["body"], "Body.\n\nSecond paragraph.")
        self.assertEqual(issue_["comments"], [{"body": "A comment."}])

    def test_a_comment_keeps_a_body_with_no_trailing_newline_byte_for_byte(self):
        self.write_issue("0001-x.md", "# X\n\nno newline at the end")
        self.comment(1, "c")
        self.assertTrue((self.dir / "0001-x.md").read_text().startswith("# X\n\nno newline at the end\n\n<!--"))

    def test_comments_are_read_in_order_and_multiline(self):
        self.create()
        self.comment(1, "one\n\nstill one\n")
        self.comment(1, "two\n")
        self.assertEqual([c["body"] for c in self.fetch(1)["comments"]], ["one\n\nstill one", "two"])

    def test_commenting_on_a_missing_issue_fails(self):
        self.create()
        body_file = self.repo.parent / "c.md"
        body_file.write_text("x")
        out = self.tracker("comment", "9", "--body-file", str(body_file), check=False)
        self.assertEqual(out.returncode, 1)

    # -- close ---------------------------------------------------------------

    def test_close_sets_the_state_and_is_idempotent(self):
        self.create()
        self.assertEqual(self.tracker("close", "1").stdout.strip(), "closed #1")
        self.assertEqual(self.fetch(1)["state"], "closed")
        text = (self.dir / "0001-first-issue.md").read_text()
        self.assertRegex(text, r"state: closed\ncreated: \d{4}-\d\d-\d\d\nclosed: \d{4}-\d\d-\d\d\n")
        again = self.tracker("close", "1")
        self.assertEqual(again.stdout.strip(), "#1 was already closed")
        self.assertEqual((self.dir / "0001-first-issue.md").read_text(), text)

    def test_close_keeps_unknown_header_keys_and_their_order_and_the_body(self):
        original = ("---\nowner: ana\nnumber: 4\ntitle: Keep me\nstate: open\nmilestone:  v2 \n"
                    "---\n\n  indented body\n\n\ttabbed\n\n<!-- issue-pilot:comment 2026-01-01T00:00:00Z -->\n\nhi\n")
        self.write_issue("0004-keep-me.md", original)
        self.tracker("close", "4")
        text = (self.dir / "0004-keep-me.md").read_text()
        self.assertRegex(text, r"^---\nowner: ana\nnumber: 4\ntitle: Keep me\nstate: closed\n"
                               r"milestone:  v2 \nclosed: \d{4}-\d\d-\d\d\n---\n")
        self.assertEqual(text.split("\n---\n", 1)[1], original.split("\n---\n", 1)[1])

    def test_closing_a_file_without_a_header_gives_it_one_and_keeps_the_text(self):
        original = "# Hand written\n\nBody.\n"
        self.write_issue("12-foo.md", original)
        self.tracker("close", "12")
        text = (self.dir / "12-foo.md").read_text()
        self.assertTrue(text.endswith("\n---\n\n" + original))
        self.assertRegex(text, r"^---\nnumber: 12\ntitle: Hand written\nstate: closed\nclosed: \d{4}-\d\d-\d\d\n---\n")
        self.assertEqual(self.fetch(12)["state"], "closed")
        self.assertEqual(self.fetch(12)["body"], "# Hand written\n\nBody.")

    # -- list ----------------------------------------------------------------

    def test_list_honours_state_and_search(self):
        self.create("Alpha bug", "crashes\n")
        self.create("Beta", "mentions the ALPHA thing\n")
        self.create("Gamma", "unrelated\n")
        self.tracker("close", "3")
        self.assertEqual(self.tracker("list").stdout,
                         "1\topen\tAlpha bug\n2\topen\tBeta\n")
        self.assertEqual(self.tracker("list", "--state", "closed").stdout, "3\tclosed\tGamma\n")
        self.assertEqual(self.tracker("list", "--state", "all").stdout.count("\n"), 3)
        self.assertEqual(self.tracker("list", "--search", "alpha").stdout,
                         "1\topen\tAlpha bug\n2\topen\tBeta\n")
        self.assertEqual(self.tracker("list", "--search", "unrelated", "--state", "all").stdout,
                         "3\tclosed\tGamma\n")

    def test_list_of_a_missing_directory_is_empty(self):
        self.assertEqual(self.tracker("list").stdout, "")

    def test_list_is_sorted_by_number_not_by_name(self):
        self.write_issue("10-b.md", "# Ten\n")
        self.write_issue("0002-a.md", "# Two\n")
        self.assertEqual([l.split("\t")[0] for l in self.tracker("list").stdout.splitlines()], ["2", "10"])

    def test_kind_reports_the_tracker(self):
        self.assertEqual(self.tracker("kind").stdout.strip(), "local")
        self.assertEqual(self.tracker("kind", env={"ISSUE_PILOT_TRACKER": "github"}).stdout.strip(), "github")

    def test_an_issues_dir_that_escapes_the_repository_is_refused(self):
        out = self.tracker("list", env={"ISSUE_PILOT_ISSUES_DIR": "../elsewhere"}, check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("issues_dir", out.stderr)


class Commit(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true", "tracker": "local"})
        self.git("push", "-q", "origin", "main")
        self.body = self.repo.parent / "body.md"
        self.body.write_text("Body.\n")

    def tracker(self, *args, check=True):
        return self.run_script("issues_tracker.py", *args, check=check)

    def create(self, title="One"):
        self.tracker("create", "--title", title, "--body-file", str(self.body))

    def files_of(self, rev="HEAD"):
        return self.git("show", "--name-only", "--format=", rev).splitlines()

    def test_commit_makes_one_commit_with_only_the_issues_even_if_something_else_is_staged(self):
        self.create()
        (self.repo / "other.txt").write_text("unrelated\n")
        self.git("add", "other.txt")
        out = self.tracker("commit", "-m", "Add issue 1")
        self.assertRegex(out.stdout.strip(), r"^committed [0-9a-f]{7,}: Add issue 1$")
        self.assertEqual(self.files_of(), ["issues/0001-one.md"])
        self.assertEqual(self.git("log", "-1", "--format=%s"), "Add issue 1")
        # What was staged for someone else is still staged, untouched.
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "other.txt")

    def test_commit_again_reports_nothing_to_commit(self):
        self.create()
        self.tracker("commit", "-m", "x")
        head = self.git("rev-parse", "HEAD")
        out = self.tracker("commit", "-m", "x")
        self.assertEqual(out.stdout.strip(), "nothing to commit: no issue changed")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_commit_with_no_issues_directory_reports_nothing_to_commit(self):
        out = self.tracker("commit", "-m", "x")
        self.assertEqual(out.stdout.strip(), "nothing to commit: no issue changed")

    def test_a_comment_and_a_close_are_committed_too(self):
        self.create()
        self.tracker("commit", "-m", "Add")
        self.tracker("close", "1")
        self.tracker("commit", "-m", "Close")
        self.assertEqual(self.files_of(), ["issues/0001-one.md"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_an_ignored_issues_directory_is_never_committed(self):
        with open(self.repo / ".git" / "info" / "exclude", "a") as out:
            out.write("issues/\n")
        self.create()
        head = self.git("rev-parse", "HEAD")
        out = self.tracker("commit", "-m", "x")
        self.assertEqual(out.stdout.strip(), "nothing to commit: issues are not kept in git here")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_the_github_tracker_commits_nothing(self):
        self.write_config({"test_command": "true", "tracker": "github"})
        (self.repo / "issues").mkdir()
        (self.repo / "issues" / "0001-x.md").write_text("# x\n")
        head = self.git("rev-parse", "HEAD")
        out = self.tracker("commit", "-m", "x")
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.strip(), "nothing to commit: issues are not kept in git here")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def test_push_sends_the_current_branch_to_origin(self):
        self.create()
        self.tracker("commit", "-m", "Add issue 1", "--push")
        self.assertEqual(self.git("-C", str(self.origin), "rev-parse", "main"), self.git("rev-parse", "HEAD"))

    def test_nothing_to_commit_means_nothing_is_pushed(self):
        self.create()
        self.tracker("commit", "-m", "Add")
        self.git("push", "-q", "origin", "main")
        before = self.git("-C", str(self.origin), "rev-parse", "main")
        self.tracker("commit", "-m", "again", "--push")
        self.assertEqual(self.git("-C", str(self.origin), "rev-parse", "main"), before)

    def test_a_refused_push_exits_1_and_says_the_commit_is_local_only(self):
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho refused by policy >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
        self.create()
        out = self.tracker("commit", "-m", "Add", "--push", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("local only", out.stderr)
        self.assertIn("before issue-pilot will start from this branch again", out.stderr)
        self.assertIn("refused by policy", out.stderr)
        # The commit itself was made.
        self.assertEqual(self.files_of(), ["issues/0001-one.md"])

    def test_push_with_no_origin_exits_0_and_says_so(self):
        self.git("remote", "remove", "origin")
        self.create()
        out = self.tracker("commit", "-m", "Add", "--push")
        self.assertEqual(out.returncode, 0)
        self.assertIn("committed", out.stdout)
        self.assertIn("origin", out.stderr)
        self.assertEqual(self.files_of(), ["issues/0001-one.md"])


class GitHub(PilotTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"test_command": "true"})
        self.body = self.repo.parent / "body.md"
        self.body.write_text("The body.\n")

    def tracker(self, *args, check=True, env=None):
        return self.run_script("issues_tracker.py", *args, check=check, env=env)

    def calls(self):
        log = pathlib.Path(self.env["FAKE_GH_ISSUE_LOG"])
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    def test_the_default_tracker_is_github(self):
        self.assertEqual(self.tracker("kind").stdout.strip(), "github")

    def test_view_returns_the_fetch_object_with_a_lowercase_state(self):
        self.write_issues({3: {**issue(3, "Three", "Body", ["c1", "c2"]), "state": "CLOSED"}})
        out = json.loads(self.tracker("view", "3", "--json").stdout)
        self.assertEqual(out, {"number": 3, "title": "Three", "body": "Body", "state": "closed",
                               "comments": [{"body": "c1"}, {"body": "c2"}]})

    def test_view_is_readable(self):
        self.write_issues({3: issue(3, "Three", "Body", ["c1"])})
        self.assertEqual(self.tracker("view", "3").stdout,
                         "#3 Three\nstate: open\n\nBody\n\n--- comment 1 ---\nc1\n")

    def test_a_missing_issue_is_an_error(self):
        out = self.tracker("view", "9", check=False)
        self.assertEqual(out.returncode, 1)
        self.assertIn("no such issue", out.stderr)

    def test_create_takes_the_number_from_the_url_and_passes_the_labels(self):
        out = self.tracker("create", "--title", "T", "--body-file", str(self.body),
                           "--label", "bug", "--label", "export")
        self.assertEqual(out.stdout.splitlines(), ["100", "https://github.com/example/repo/issues/100"])
        (call,) = self.calls()
        self.assertEqual(call["cmd"], "create")
        self.assertEqual(call["body"], "The body.\n")
        self.assertEqual(call["args"][:2], ["--title", "T"])
        labels = [call["args"][i + 1] for i, a in enumerate(call["args"]) if a == "--label"]
        self.assertEqual(labels, ["bug", "export"])
        second = self.tracker("create", "--title", "T2", "--body-file", str(self.body))
        self.assertEqual(second.stdout.splitlines()[0], "101")

    def test_comment_and_close_are_logged(self):
        self.assertEqual(self.tracker("comment", "5", "--body-file", str(self.body)).stdout.strip(),
                         "commented on #5")
        self.assertEqual(self.tracker("close", "5").stdout.strip(), "closed #5")
        comment, close = self.calls()
        self.assertEqual((comment["cmd"], comment["args"][0], comment["body"]), ("comment", "5", "The body.\n"))
        self.assertEqual((close["cmd"], close["args"]), ("close", ["5"]))

    def test_list_parses_the_json_and_sorts_by_number(self):
        listing = [{"number": 9, "title": "Nine", "state": "OPEN"},
                   {"number": 2, "title": "Two", "state": "OPEN"}]
        out = self.tracker("list", "--search", "x", env={"FAKE_GH_ISSUE_LIST": json.dumps(listing)})
        self.assertEqual(out.stdout, "2\topen\tTwo\n9\topen\tNine\n")

    def test_list_with_nothing_prints_nothing(self):
        self.assertEqual(self.tracker("list").stdout, "")

    def test_without_gh_the_error_says_so(self):
        # A PATH with git and nothing else, so it is `gh` that is missing.
        bare = self.repo.parent / "bare-bin"
        bare.mkdir()
        (bare / "git").symlink_to(shutil.which("git"))
        out = self.run_script("issues_tracker.py", "view", "1", check=False,
                              env={"PATH": str(bare)})
        self.assertEqual(out.returncode, 1)
        self.assertIn("(`gh`) is not installed", out.stderr)


if __name__ == "__main__":
    unittest.main()
