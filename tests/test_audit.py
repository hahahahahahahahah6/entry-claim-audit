import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from entry_claim_audit.audit import audit, fallback_metadata
from entry_claim_audit.cli import main


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('pyproject.toml', '[project]\nname="my-tool"\n[project.scripts]\nmytool="pkg.cli:main"\n')
        self.write('pkg/cli.py', 'def main(): pass\n')
        self.write('README.md', '`mytool --help`\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def target(self, target, table='scripts', name='mytool'):
        self.write('pyproject.toml', '[project]\nname="my-tool"\n[project.' + table + ']\n' + name + '=' + json.dumps(target) + '\n')

    def check(self, rule=None, severity=None, **kwargs):
        result = audit(self.root, **kwargs)
        self.assertEqual([f['rule'] for f in result['findings']], [rule] if rule else [])
        if severity:
            self.assertEqual(result['findings'][0]['severity'], severity)
        return result

    def cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([str(self.root), *args])
        return code, output.getvalue()

    def test_good(self):
        self.assertEqual(self.check()['status'], 'PASS')

    def test_good_cli(self):
        self.assertEqual(self.cli()[0], 0)

    def test_verified(self):
        self.assertEqual(self.check()['verified_scripts'], [dict(script='mytool', table='scripts', target='pkg.cli:main')])

    def test_missing_module(self):
        self.target('pkg.nosuch:main')
        self.check('script-target-module-missing', 'HIGH')
        self.assertEqual(self.cli()[0], 2)

    def test_missing_symbol(self):
        self.write('pkg/cli.py', 'other = 1')
        self.check('script-target-symbol-missing', 'HIGH')

    def test_constant(self):
        self.write('pkg/cli.py', 'main = 42')
        self.assertEqual(self.check('script-target-not-callable', 'MEDIUM')['status'], 'FAIL')

    def test_high_threshold(self):
        self.write('pkg/cli.py', 'main = 42')
        self.assertEqual(self.check('script-target-not-callable', fail_on='HIGH')['status'], 'PASS')
        self.assertEqual(self.cli('--fail-on', 'HIGH')[0], 0)

    def test_no_colon(self):
        self.target('pkg.cli.main')
        self.check('script-target-malformed', 'HIGH')

    def test_empty_module(self):
        self.target(':main')
        self.check('script-target-malformed')

    def test_empty_object(self):
        self.target('pkg.cli:')
        self.check('script-target-malformed')

    def test_internal_space(self):
        self.target('pkg.cli:ma in')
        self.check('script-target-malformed')

    def test_surrounding_space(self):
        self.target('  pkg.cli:main  ')
        self.check()

    def test_double_colon(self):
        self.target('pkg.cli::main')
        self.check('script-target-malformed')

    def test_invalid_dotted(self):
        self.target('pkg..cli:main')
        self.check('script-target-malformed')

    def test_attr_missing(self):
        self.write('pkg/cli.py', 'class Runner: pass')
        self.target('pkg.cli:Runner.run')
        self.check('script-target-attr-missing', 'HIGH')

    def test_attr_present(self):
        self.write('pkg/cli.py', 'class Runner:\n    def run(self): pass')
        self.target('pkg.cli:Runner.run')
        self.check()

    def test_deep_attr(self):
        self.write('pkg/cli.py', 'class Runner:\n    def run(self): pass')
        self.target('pkg.cli:Runner.run.unknown')
        self.check()

    def test_function_attr(self):
        self.target('pkg.cli:main.unknown')
        self.check()

    def test_class_callable(self):
        self.write('pkg/cli.py', 'class main: pass')
        self.check()

    def test_async_callable(self):
        self.write('pkg/cli.py', 'async def main(): pass')
        self.check()

    def test_reexport(self):
        self.write('pkg/__init__.py', 'from .cli import main')
        self.target('pkg:main')
        self.check()

    def test_import_alias(self):
        self.write('pkg/cli.py', 'import nonexistent as main')
        self.check()

    def test_import_name(self):
        self.write('pkg/cli.py', 'import main.submodule')
        self.check()

    def test_lambda(self):
        self.write('pkg/cli.py', 'main = lambda: None')
        self.check()

    def test_call(self):
        self.write('pkg/cli.py', 'main = factory()')
        self.check()

    def test_name_assignment(self):
        self.write('pkg/cli.py', 'main = other')
        self.check('script-target-not-callable')

    def test_attribute_assignment(self):
        self.write('pkg/cli.py', 'main = other.attr')
        self.check('script-target-not-callable')

    def test_annotated(self):
        self.write('pkg/cli.py', 'main: int = 42')
        self.check('script-target-not-callable')

    def test_tests_only(self):
        self.write('tests/cli.py', 'def main(): pass')
        self.target('tests.cli:main')
        self.check('script-target-module-missing', 'HIGH')

    def test_src_module(self):
        (self.root / 'pkg/cli.py').unlink()
        self.write('src/pkg/cli.py', 'def main(): pass')
        self.check()

    def test_src_package(self):
        self.write('src/other/__init__.py', 'def main(): pass')
        self.target('other:main')
        self.check()

    def test_flat_single(self):
        self.write('single.py', 'def main(): pass')
        self.target('single:main')
        self.check()

    def test_src_single(self):
        self.write('src/single.py', 'def main(): pass')
        self.target('single:main')
        self.check()

    def test_nested_package(self):
        self.write('pkg/sub/__init__.py', 'def main(): pass')
        self.target('pkg.sub:main')
        self.check()

    def test_documented_missing(self):
        self.write('README.md', 'mytool\n```sh\nmy-tool --help\n```')
        self.check('documented-command-missing', 'HIGH')

    def test_documented_inline(self):
        self.write('README.md', 'mytool and `my-tool --help`')
        self.check('documented-command-missing')

    def test_normalized_name(self):
        self.write('README.md', 'mytool and `MY_TOOL --help`')
        self.check('documented-command-missing')

    def test_prose_no_command(self):
        self.write('README.md', 'mytool my-tool --help')
        self.check()

    def test_arbitrary_command(self):
        self.write('README.md', 'mytool `pip install x` `git status`')
        self.check()

    def test_undocumented(self):
        self.write('README.md', 'Nothing here')
        self.check('script-undocumented', 'MEDIUM')

    def test_docsless(self):
        (self.root / 'README.md').unlink()
        self.check()

    def test_duplicate(self):
        self.write('pyproject.toml', '[project.scripts]\nmytool="pkg.cli:main"\n[project.gui-scripts]\nmytool="pkg.cli:main"')
        self.check('script-name-duplicate', 'MEDIUM')

    def test_gui_bad(self):
        self.target('pkg.nosuch:main', 'gui-scripts')
        self.check('script-target-module-missing', 'HIGH')

    def test_gui_good(self):
        self.target('pkg.cli:main', 'gui-scripts')
        self.check()

    def test_no_scripts(self):
        self.write('pyproject.toml', '[project]\nname="library"')
        self.check()
        self.assertEqual(self.cli()[0], 0)

    def test_no_pyproject(self):
        (self.root / 'pyproject.toml').unlink()
        self.check()
        self.assertEqual(self.cli()[0], 0)

    def test_no_project(self):
        self.write('pyproject.toml', '[tool.example]\na=1')
        self.check()

    def test_ignore(self):
        self.target('missing:main')
        self.check(ignore=['script-target-module-missing'])
        self.assertEqual(self.cli('--ignore', 'script-target-module-missing')[0], 0)

    def test_json_sorted(self):
        self.target('missing:main')
        self.write('README.md', '`my-tool --help`')
        code, output = self.cli('--json')
        result = json.loads(output)
        self.assertEqual(code, 2)
        self.assertEqual(result['findings'], sorted(result['findings'], key=lambda f: (f['rule'], f['file'], f['line'])))
        self.assertEqual(output, self.cli('--json')[1])

    def test_version(self):
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                main(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), '0.1.0')

    def test_usage_error(self):
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                main(['--fail-on', 'LOW'])
        self.assertEqual(caught.exception.code, 1)

    def test_nested_doc(self):
        self.write('README.md', 'Introduction')
        self.write('docs/deep/usage.md', '`mytool --help`')
        self.check()

    def test_whole_word(self):
        self.write('README.md', 'mytools')
        self.check('script-undocumented')

    def test_no_execution(self):
        self.write('pkg/cli.py', 'raise RuntimeError()\ndef main(): pass')
        self.check()

    def test_nested_symbol_excluded(self):
        self.write('pkg/cli.py', 'def outer():\n    def main(): pass')
        self.check('script-target-symbol-missing')

    def test_fallback(self):
        with patch('entry_claim_audit.audit.tomllib', None):
            self.check()

    def test_fallback_gui(self):
        self.target('pkg.cli:main', 'gui-scripts')
        with patch('entry_claim_audit.audit.tomllib', None):
            self.check()

    def test_invalid_toml(self):
        self.write('pyproject.toml', '[broken')
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                self.cli()
        self.assertEqual(caught.exception.code, 1)

    def test_verbose(self):
        self.assertIn('verified mytool', self.cli('--verbose')[1])

    def test_tilde_fence(self):
        self.write('README.md', 'mytool\n~~~sh\nmy-tool --help\n~~~')
        self.check('documented-command-missing')

    def test_no_scripts_with_project_command(self):
        self.write('pyproject.toml', '[project]\nname="my-tool"')
        self.write('README.md', '`my-tool --help`')
        self.check()

    def test_env_never_read(self):
        self.write('.env', 'placeholder only')
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            self.assertNotEqual(path.name, '.env')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', guarded):
            self.check()

    def test_dist_not_evidence(self):
        self.write('dist/cli.py', 'def main(): pass')
        self.target('dist.cli:main')
        self.check('script-target-module-missing')


if __name__ == '__main__':
    unittest.main()
