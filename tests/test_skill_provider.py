from arf.resources.providers.skill_provider import SkillProvider


class TestList:
    def test_lists_all_skills(self, temp_root, skill_yaml):
        skill_yaml("code_review")
        skill_yaml("debug")

        p = SkillProvider(temp_root)
        skills = p.list()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"code_review", "debug"}

    def test_empty_dir_returns_empty(self, temp_root):
        p = SkillProvider(temp_root)
        assert p.list() == []

    def test_subdirectory_format(self, temp_root):
        """SkillProvider must find skills in subdirectories (sales-report/skill.yaml)."""
        (temp_root / "sales-report").mkdir()
        (temp_root / "sales-report" / "skill.yaml").write_text(
            "name: sales-report\ndescription: Generate sales reports\n",
            encoding="utf-8",
        )
        p = SkillProvider(temp_root)
        skills = p.list()
        assert len(skills) == 1
        assert skills[0].name == "sales-report"
        assert skills[0].description == "Generate sales reports"


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
        after = p.list()
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
