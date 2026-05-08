# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "questionary",
#     "rich",
#     "psutil",
# ]
# ///

import unittest
import sys
from unittest.mock import patch, MagicMock, call

# Mock dependencies before importing nexus_deploy
rich = MagicMock()
sys.modules["rich"] = rich
sys.modules["rich.console"] = MagicMock()
sys.modules["rich.panel"] = MagicMock()
sys.modules["rich.text"] = MagicMock()
sys.modules["rich.prompt"] = MagicMock()
sys.modules["rich.table"] = MagicMock()
sys.modules["rich.progress"] = MagicMock()
sys.modules["rich.layout"] = MagicMock()

questionary = MagicMock()
sys.modules["questionary"] = questionary

psutil = MagicMock()
sys.modules["psutil"] = psutil

import subprocess
from pathlib import Path
import shutil
import nexus_deploy

class TestNexusDeploy(unittest.TestCase):
    def setUp(self):
        # Reset any global state if necessary
        pass

    @patch('nexus_deploy.console.print')
    @patch('subprocess.run')
    def test_stow_directory_success(self, mock_run, mock_print):
        # Mock successful subprocess run
        mock_run.return_value = MagicMock(returncode=0)

        nexus_deploy.stow_directory("/tmp/source", "test-package")

        mock_run.assert_called_once()
        # Verify success message
        mock_print.assert_any_call("[green]Successfully stowed test-package[/green]")

    @patch('nexus_deploy.console.print')
    @patch('subprocess.run')
    def test_stow_directory_failure(self, mock_run, mock_print):
        # Mock subprocess run raising CalledProcessError
        error_message = "Stow error details"
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd="stow",
            stderr=error_message
        )

        nexus_deploy.stow_directory("/tmp/source", "test-package")

        mock_run.assert_called_once()
        # Verify red error message (targets line 466-467)
        mock_print.assert_any_call(f"[red]Stow failed: {error_message}[/red]")

    @patch('nexus_deploy.console.print')
    @patch('subprocess.run')
    @patch('nexus_deploy.PROFILES', {"Test Profile": {"repo": "https://example.com/repo.git"}})
    @patch('nexus_deploy.DOTFILES_DIR', Path("/tmp/.nexus_dotfiles"))
    @patch('shutil.rmtree')
    @patch('pathlib.Path.exists')
    def test_clone_and_stow_profile_failure(self, mock_exists, mock_rmtree, mock_run, mock_print):
        mock_exists.return_value = False
        # Mock git clone failure
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd="git clone")

        nexus_deploy.clone_and_stow_profile("Test Profile")

        # Verify red failure message (targets line 483-484)
        mock_print.assert_any_call("[red]Failed to clone repository.[/red]")

    @patch('nexus_deploy.console.print')
    @patch('subprocess.run')
    @patch('shutil.which')
    def test_apply_opsec_hardening_ufw_failure(self, mock_which, mock_run, mock_print):
        # Mock ufw exists but fails
        mock_which.side_effect = lambda x: True if x == "ufw" else False

        # Mock CalledProcessError for the first ufw command
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd=["sudo", "ufw", "reset"],
            stderr="UFW error"
        )

        nexus_deploy.apply_opsec_hardening()

        # Verify red error message (targets line 613-614)
        # Note: the code prints f"[red]Failed to configure UFW: {e}[/red]"
        # Where {e} is the exception string representation
        expected_error_regex = r"Failed to configure UFW: Command '.*' returned non-zero exit status 1."

        # Check if any call matches our expectation
        found = False
        for call_args in mock_print.call_args_list:
            if "Failed to configure UFW" in str(call_args):
                found = True
                break
        self.assertTrue(found, "UFW failure message not printed")

    @patch('nexus_deploy.console.print')
    @patch('subprocess.run')
    @patch('shutil.which')
    def test_apply_opsec_hardening_macchanger_failure(self, mock_which, mock_run, mock_print):
        # Mock macchanger exists, but ip link fails or macchanger fails
        def side_effect_which(cmd):
            if cmd in ["macchanger", "ip"]: return True
            return False
        mock_which.side_effect = side_effect_which

        # Mock ip link output to return one interface
        mock_ip_link = MagicMock()
        mock_ip_link.stdout = "1: lo: <LOOPBACK>\n2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT group default qlen 1000\n    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff"

        # We need a side effect for mock_run to handle multiple calls
        def side_effect_run(cmd, **kwargs):
            if cmd == ["ip", "link"]:
                return mock_ip_link
            if "macchanger" in cmd or ("ip" in cmd and "set" in cmd):
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="MAC error")
            return MagicMock(returncode=0)

        mock_run.side_effect = side_effect_run

        nexus_deploy.apply_opsec_hardening()

        # Verify red error message (targets line 639-640)
        found = False
        for call_args in mock_print.call_args_list:
            if "Failed to spoof MAC for eth0" in str(call_args):
                found = True
                break
        self.assertTrue(found, "MAC spoofing failure message not printed")

if __name__ == '__main__':
    unittest.main()
