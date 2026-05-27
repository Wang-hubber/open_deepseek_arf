from arf.resources.providers.skill_provider import SkillProvider


class TestList:
    def test_lists_all_skills(self, temp_root, skill_yaml):
        skill_yaml("code_review", activation="kernel")
        skill_yaml("debug", activation="discoverable")

        p = SkillProvider(temp_root)
        skills = p.list()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"code_review", "debug"}

    def test_empty_dir_returns_empty(self, temp_root):
        p = SkillProvider(temp_root)
        assert p.list() == []


class TestSplitKernelDynamic:
    def test_splits_by_activation(self, temp_root, skill_yaml):
        skill_yaml("code_review", activation="kernel")
        skill_yaml("debug", activation="discoverable")

        p = SkillProvider(temp_root)
        kernel = p.list_kernel()
        dynamic = p.list_dynamic()

        assert {s.name for s in kernel} == {"code_review"}
        assert {s.name for s in dynamic} == {"debug"}

    def test_empty_dir_returns_empty_both(self, temp_root):
        p = SkillProvider(temp_root)
        assert p.list_kernel() == []
        assert p.list_dynamic() == []


class TestCaching:
    def test_second_list_uses_cache(self, temp_root, skill_yaml):
        skill_yaml("s1")
        p = SkillProvider(temp_root)
        first = p.list()
        # Add file after scan — should not appear (cache active)
        skill_yaml("s2")
        second = p.list()
        assert len(second) == 1


class TestInvalidateDynamic:
    def test_invalidate_rescans(self, temp_root, skill_yaml):
        skill_yaml("s1")
        p = SkillProvider(temp_root)
        p.list()  # populate cache
        skill_yaml("s2")
        p.invalidate_dynamic()
        after = p.list_dynamic()
        assert {s.name for s in after} == {"s1", "s2"}


class TestInvalidYaml:
    def test_missing_name_is_skipped(self, temp_root):
        p = temp_root / "bad.yaml"
        p.write_text("description: no name field", encoding="utf-8")
        provider = SkillProvider(temp_root)
        assert provider.list() == []

    def test_empty_file_is_skipped(self, temp_root):
        p = temp_root / "empty.yaml"
        p.write_text("", encoding="utf-8")
        provider = SkillProvider(temp_root)
        assert provider.list() == []
