import pytest

from praas.tools.pr_similar_issue import PRSimilarIssue


def test_pinecone_indexing_uses_instance_dependencies():
    class FakePandas:
        @staticmethod
        def DataFrame(documents):
            raise RuntimeError("instance pandas dependency used")

    tool = PRSimilarIssue.__new__(PRSimilarIssue)
    tool.max_issues_to_scan = 1
    tool._pandas = FakePandas

    with pytest.raises(RuntimeError, match="instance pandas dependency used"):
        tool._update_index_with_issues([], "example-repository")


def test_lancedb_ingest_adds_to_existing_table(monkeypatch):
    class FakeFrame:
        def __init__(self):
            self.columns = {}

        def __getitem__(self, key):
            assert key == "text"
            return type("TextColumn", (), {"values": ["example_issue"]})()

        def __setitem__(self, key, value):
            self.columns[key] = value

    class FakePandas:
        @staticmethod
        def DataFrame(_documents):
            return FakeFrame()

    class FakeTable:
        def __init__(self):
            self.added = []

        def add(self, frame):
            self.added.append(frame)

    class FakeDatabase:
        @staticmethod
        def table_names():
            return ["praas-issues"]

    class FakeSettings:
        class openai:
            key = "test-key"

    fake_table = FakeTable()
    tool = PRSimilarIssue.__new__(PRSimilarIssue)
    tool.max_issues_to_scan = 1
    tool._pandas = FakePandas
    tool.index_name = "praas-issues"
    tool.db = FakeDatabase()
    tool.table = fake_table

    monkeypatch.setattr("praas.tools.pr_similar_issue.get_settings", lambda: FakeSettings)
    monkeypatch.setattr(
        "praas.tools.pr_similar_issue.openai.Embedding.create",
        lambda **_kwargs: {"data": [{"embedding": [1.0]}]},
    )
    monkeypatch.setattr("praas.tools.pr_similar_issue.time.sleep", lambda _seconds: None)

    tool._update_table_with_issues([], "example-repository", ingest=True)

    assert len(fake_table.added) == 1


@pytest.mark.asyncio
async def test_similar_issue_non_github_publishes_message(monkeypatch):
    class FakeProvider:
        def __init__(self):
            self.comments = []

        def publish_comment(self, body):
            self.comments.append(body)

    fake_provider = FakeProvider()

    class FakeSettings:
        class config:
            git_provider = "gitlab"
            publish_output = True

    monkeypatch.setattr("praas.tools.pr_similar_issue.get_settings", lambda: FakeSettings)
    monkeypatch.setattr(
        "praas.git_providers.get_git_provider_with_context",
        lambda _: fake_provider,
    )

    tool = PRSimilarIssue("https://gitlab.example.com/group/repo/-/merge_requests/1", None)
    result = await tool.run()

    assert result == ""
    assert fake_provider.comments == [
        "The /similar_issue tool is currently supported only for GitHub."
    ]


@pytest.mark.asyncio
async def test_similar_issue_non_github_no_publish(monkeypatch):
    class FakeSettings:
        class config:
            git_provider = "gitlab"
            publish_output = False

    monkeypatch.setattr("praas.tools.pr_similar_issue.get_settings", lambda: FakeSettings)

    tool = PRSimilarIssue("https://gitlab.example.com/group/repo/-/merge_requests/1", None)
    result = await tool.run()

    assert result == ""
