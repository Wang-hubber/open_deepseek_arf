from arf.resources.providers.model_provider import ModelProvider


class TestList:
    def test_lists_all_models(self, temp_root, model_yaml):
        model_yaml("quick", model_type="quick", activation="kernel")
        model_yaml("deep", model_type="deep", activation="discoverable")

        p = ModelProvider(temp_root)
        models = p.list()
        assert len(models) == 2
        types = {m.type for m in models}
        assert types == {"quick", "deep"}

    def test_empty_dir_returns_empty(self, temp_root):
        p = ModelProvider(temp_root)
        assert p.list() == []


class TestSplitKernelDynamic:
    def test_splits_by_activation(self, temp_root, model_yaml):
        model_yaml("quick_one", model_type="quick", activation="kernel")
        model_yaml("deep_one", model_type="deep", activation="discoverable")

        p = ModelProvider(temp_root)
        kernel = p.list_kernel()
        dynamic = p.list_dynamic()

        assert {m.type for m in kernel} == {"quick"}
        assert {m.type for m in dynamic} == {"deep"}

    def test_empty_dir_returns_empty_both(self, temp_root):
        p = ModelProvider(temp_root)
        assert p.list_kernel() == []
        assert p.list_dynamic() == []


class TestInvalidateDynamic:
    def test_invalidate_rescans(self, temp_root, model_yaml):
        model_yaml("quick")
        p = ModelProvider(temp_root)
        p.list()
        model_yaml("deep", model_type="deep")
        p.invalidate_dynamic()
        assert {m.type for m in p.list_dynamic()} == {"quick", "deep"}


class TestInvalidYaml:
    def test_missing_type_is_skipped(self, temp_root):
        p = temp_root / "bad.yaml"
        p.write_text("name: no-type-field\napi_key_env: X", encoding="utf-8")
        provider = ModelProvider(temp_root)
        assert provider.list() == []
