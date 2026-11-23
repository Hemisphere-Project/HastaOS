import os
import tempfile
import unittest

from hasta import facts
from tests.helpers import root


class FactsOnFixtureTrees(unittest.TestCase):

    def test_pi3_buster(self):
        f = facts.compute(root("pi3-buster"))
        v = f.values
        self.assertFalse(f.live)
        self.assertEqual(v["machine"], "rpi3")
        self.assertEqual(v["model"], "Raspberry Pi 3 Model B Plus Rev 1.3")
        self.assertEqual(v["architecture"], "armhf")
        self.assertEqual(v["base"]["name"], "raspbian-buster")
        self.assertEqual(v["base"]["version_id"], "10")
        self.assertEqual(v["kernels"], ["5.10.103+", "5.10.103-v7+"])
        self.assertIsNone(v["kernel"])  # not running: --root never reads uname
        self.assertEqual(v["boot"], {"path": "/boot", "kind": "firmware"})
        self.assertEqual(v["data"], "PARTUUID=0a1b2c3d-03")
        self.assertEqual(v["graphics"], "legacy")  # the [pi4] fkms line does not apply to a Pi 3
        # The receipt's tag wins over /boot/VERSION, which 7.4 left at "7.0 -- bootstraped".
        self.assertEqual(v["stack"], {"name": "rasta", "revision": "7.5", "inferred": True})
        self.assertEqual(f.sources["stack"], "/etc/rastaos-release")
        self.assertEqual(v["release"]["RASTAOS_TAG"], "rastaos-7.5")

    def test_pi4_boot_firmware(self):
        f = facts.compute(root("pi4-bookworm"))
        v = f.values
        self.assertEqual(v["machine"], "rpi4")
        self.assertEqual(v["architecture"], "arm64")
        self.assertEqual(v["base"]["name"], "debian-bookworm")  # via an absolute symlink
        self.assertEqual(v["kernels"], ["6.6.51+rpt-rpi-v8"])    # /lib -> usr/lib
        self.assertEqual(v["boot"], {"path": "/boot/firmware", "kind": "firmware"})
        self.assertEqual(v["data"], "LABEL=data")
        self.assertEqual(v["graphics"], "kms")
        self.assertIsNone(v["stack"])
        self.assertIsNone(v["release"])

    def test_x86_esp(self):
        f = facts.compute(root("x86-esp"))
        v = f.values
        self.assertEqual(v["machine"], "x86-uefi")
        self.assertEqual(v["model"], "AZW MINI S")
        self.assertEqual(v["architecture"], "amd64")
        self.assertEqual(v["base"]["name"], "debian-trixie")
        self.assertEqual(v["boot"], {"path": "/boot/efi", "kind": "esp"})
        self.assertEqual(v["data"], "UUID=c0ffee00-0000-4000-8000-000000000003")
        self.assertEqual(v["graphics"], "x86")
        self.assertIsNone(v["stack"])

    def test_card_in_a_reader(self):
        # No /proc, no receipt: a Pi of unknown model, Rasta inferred from /boot/VERSION.
        f = facts.compute(root("rasta-card"))
        v = f.values
        self.assertEqual(v["machine"], "rpi")
        self.assertEqual(v["base"]["name"], "raspbian-buster")  # codename from VERSION="10 (buster)"
        self.assertEqual(v["stack"], {"name": "rasta", "revision": "7.5", "inferred": True})
        self.assertEqual(f.sources["stack"], "/boot/VERSION")
        self.assertIsNone(v["architecture"])
        self.assertIsNone(v["data"])

    def test_declared_stack_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "etc", "hasta"))
            with open(os.path.join(tmp, "etc", "hasta", "stack"), "w") as fh:
                fh.write("STACK=nasta\nREVISION=1.0\n")
            v = facts.compute(tmp).values
            self.assertEqual(v["stack"], {"name": "nasta", "revision": "1.0", "inferred": False})
            self.assertIsNone(v["machine"])

    def test_absolute_symlink_stays_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "etc"))
            os.symlink("/etc/hostname-that-is-not-in-the-tree", os.path.join(tmp, "etc", "x"))
            self.assertEqual(facts.resolve(tmp, "/etc/x"),
                             os.path.join(tmp, "etc/hostname-that-is-not-in-the-tree"))
            self.assertIsNone(facts.read_text(tmp, "/etc/x"))

    def test_every_fact_has_a_source_when_set(self):
        for name in ("pi3-buster", "pi4-bookworm", "x86-esp", "rasta-card"):
            f = facts.compute(root(name))
            for key, value in f.values.items():
                if value not in (None, []):
                    self.assertTrue(f.sources.get(key), "%s: %s has no source" % (name, key))


class PiModels(unittest.TestCase):

    def test_model_strings(self):
        cases = {
            "Raspberry Pi 3 Model B Rev 1.2": "rpi3",
            "Raspberry Pi 3 Model A Plus Rev 1.0": "rpi3",
            "Raspberry Pi Zero 2 W Rev 1.0": "rpi3",
            "Raspberry Pi 4 Model B Rev 1.5": "rpi4",
            "Raspberry Pi 400 Rev 1.0": "rpi4",
            "Raspberry Pi Compute Module 4 Rev 1.0": "rpi4",
            "Raspberry Pi 5 Model B Rev 1.0": "rpi5",
            "Raspberry Pi 2 Model B Rev 1.1": "rpi2",
            "Raspberry Pi Zero W Rev 1.1": "rpi0",
        }
        for model, want in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                os.makedirs(os.path.join(tmp, "proc", "device-tree"))
                with open(os.path.join(tmp, "proc", "device-tree", "model"), "w") as fh:
                    fh.write(model + "\x00")
                self.assertEqual(facts.compute(tmp).values["machine"], want, model)


if __name__ == "__main__":
    unittest.main()
