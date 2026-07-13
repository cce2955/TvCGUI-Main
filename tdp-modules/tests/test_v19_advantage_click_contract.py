from __future__ import annotations

import ast
import unittest

from tests.v19_contract_helpers import function_source, read, sha256, tree

MAIN = "main.py"
COMPONENTS = "tvcgui/ui/components.py"
ADV = "tvcgui/ui/advantage_window.py"
ADV_DUP = "tdp-modules/tvcgui/ui/advantage_window.py"


class V19AdvantageClickContractTests(unittest.TestCase):
    def test_advantage_module_exists_in_primary_tree(self):
        self.assertIn("def open_advantage_window", read(ADV))

    def test_advantage_duplicate_matches_primary_exactly(self):
        self.assertEqual(sha256(ADV), sha256(ADV_DUP))

    def test_bottom_tabs_include_normals_preview(self):
        self.assertIn('(\"scan\", \"Normals Preview\", GUI_APP_ACCENT)', read(COMPONENTS))

    def test_bottom_tabs_include_advantage(self):
        self.assertIn('(\"advantage\", \"Advantage\", GUI_APP_ACCENT)', read(COMPONENTS))

    def test_advantage_is_directly_after_normals_preview(self):
        source = read(COMPONENTS)
        scan = source.index('(\"scan\", \"Normals Preview\", GUI_APP_ACCENT)')
        advantage = source.index('(\"advantage\", \"Advantage\", GUI_APP_ACCENT)')
        events = source.index('(\"events\", \"Events\", GUI_APP_ACCENT)')
        self.assertLess(scan, advantage)
        self.assertLess(advantage, events)

    def test_tab_drawer_returns_click_rectangles(self):
        source = function_source(COMPONENTS, "draw_bottom_workspace_tabs")
        self.assertIn("tab_rects[key] = tr", source)
        self.assertIn("return content, tab_rects", source)

    def test_main_receives_bottom_tab_rectangles(self):
        self.assertIn("bottom_content_rect, bottom_tab_rects = draw_bottom_workspace_tabs", read(MAIN))

    def test_main_checks_each_bottom_tab_rectangle(self):
        source = read(MAIN)
        self.assertIn("for _tab_key, _tab_rect in list(bottom_tab_rects.items()):", source)
        self.assertIn("if _tab_rect.collidepoint(mx, my):", source)

    def test_advantage_click_branch_is_explicit(self):
        self.assertIn('if _tab_key == "advantage":', read(MAIN))

    def test_click_message_is_exact_and_flushed(self):
        self.assertIn('print("[advantage] click received, opening Advantage Matrix", flush=True)', read(MAIN))

    def test_click_message_precedes_popup_import(self):
        source = read(MAIN)
        click = source.index('[advantage] click received, opening Advantage Matrix')
        popup_import = source.index("from tvcgui.ui.advantage_window import open_advantage_window", click)
        self.assertLess(click, popup_import)

    def test_click_handler_imports_primary_popup(self):
        self.assertIn("from tvcgui.ui.advantage_window import open_advantage_window", read(MAIN))

    def test_click_handler_passes_scan_and_live_slots(self):
        self.assertIn("open_advantage_window(last_scan_normals, render_snap_by_slot)", read(MAIN))

    def test_popup_call_precedes_click_consumption(self):
        source = read(MAIN)
        call = source.index("open_advantage_window(last_scan_normals, render_snap_by_slot)")
        consume = source.index("mouse_clicked_pos = None", call)
        self.assertLess(call, consume)

    def test_popup_failure_prints_full_traceback(self):
        source = read(MAIN)
        self.assertIn('print("[advantage] popup creation failed", flush=True)', source)
        self.assertIn("traceback.print_exc()", source[source.index("[advantage] popup creation failed"):])

    def test_advantage_branch_does_not_only_select_blank_tab(self):
        source = read(MAIN)
        branch_start = source.index('if _tab_key == "advantage":')
        branch_end = source.index("elif active_bottom_tab != _tab_key:", branch_start)
        branch = source[branch_start:branch_end]
        self.assertIn("open_advantage_window", branch)
        self.assertNotIn('active_bottom_tab = "advantage"', branch)

    def test_main_has_traceback_import_available(self):
        parsed = tree(MAIN)
        imports = set()
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
        self.assertIn("traceback", imports)

    def test_default_bottom_tab_remains_normals_preview(self):
        self.assertIn('active_bottom_tab = "scan"', read(MAIN))

    def test_advantage_popup_title_is_exact(self):
        self.assertIn('win.title("Advantage Matrix")', read(ADV))

    def test_popup_shell_is_created_before_data_load(self):
        source = function_source(ADV, "open_advantage_window")
        self.assertLess(source.index("win = tk.Toplevel(master_root)"), source.index("load_observed_advantage_data(force=True)"))

    def test_loading_label_is_created_before_data_load(self):
        source = function_source(ADV, "open_advantage_window")
        self.assertLess(source.index('text="Loading observed frame data..."'), source.index("load_observed_advantage_data(force=True)"))

    def test_empty_data_has_visible_message(self):
        self.assertIn("No observed frame-data profiles were found.", read(ADV))

    def test_data_load_failure_has_visible_message(self):
        self.assertIn("Advantage data failed to load.", read(ADV))

    def test_popup_close_protocol_is_installed(self):
        self.assertIn('win.protocol("WM_DELETE_WINDOW", _close)', read(ADV))

    def test_popup_close_clears_global_reference(self):
        source = function_source(ADV, "open_advantage_window")
        self.assertIn("_ADV_TK_WIN = None", source)

    def test_repeated_open_destroys_existing_window(self):
        source = function_source(ADV, "open_advantage_window")
        self.assertIn("if old is not None and bool(old.winfo_exists()):", source)
        self.assertIn("old.destroy()", source)

    def test_open_routes_through_shared_tk_host(self):
        source = function_source(ADV, "open_advantage_window")
        self.assertIn("tk_call(_show)", source)


if __name__ == "__main__":
    unittest.main()
