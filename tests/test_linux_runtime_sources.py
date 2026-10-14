"""Linux replacement must retain the complete versioned ABI, not just names."""
import unittest

from ci.linux_runtime_sources import exports


class RuntimeExportsTests(unittest.TestCase):
    def test_versioned_abi_excludes_undefined_and_local_symbols(self):
        text = '''
  1: 00000001 50 FUNC GLOBAL DEFAULT 12 sqrtq@@QUADMATH_1.0
  2: 00000000 0 FUNC GLOBAL DEFAULT UND expq@QUADMATH_1.0
  3: 00000002 20 FUNC LOCAL DEFAULT 12 helper
  4: 00000003 30 FUNC WEAK DEFAULT 12 logq@QUADMATH_1.0
'''
        self.assertEqual(exports(text), {'sqrtq@QUADMATH_1.0', 'logq@QUADMATH_1.0'})

    def test_matching_names_with_wrong_symbol_versions_are_not_equivalent(self):
        old = exports('1: 00000001 50 FUNC GLOBAL DEFAULT 12 sqrtq@@QUADMATH_1.0')
        new = exports('1: 00000001 50 FUNC GLOBAL DEFAULT 12 sqrtq@@QUADMATH_2.0')
        self.assertEqual(old - new, {'sqrtq@QUADMATH_1.0'})


if __name__ == '__main__': unittest.main()
