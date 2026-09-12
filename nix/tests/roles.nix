{ pkgs }:
let
  fixture = pkgs.runCommand "bcenv-test-repository" { nativeBuildInputs = [ pkgs.git ]; } ''
    mkdir -p $out
    cd $out
    git init -q
    git config user.name Fixture
    git config user.email fixture@example.invalid
    echo 'fixture bot' > bot.txt
    git add bot.txt
    GIT_AUTHOR_DATE=2026-01-01T00:00:00Z GIT_COMMITTER_DATE=2026-01-01T00:00:00Z git commit -qm baseline
    git rev-parse HEAD > revision
  '';
  competitor = { pkgs, ... }: {
    imports = [ ../modules/competitor.nix ];
    environment.etc."bcenv/fixture".source = fixture;
    # The read-only test origin is owned by root in the Nix store.
    programs.git.enable = true;
    programs.git.config.safe.directory = [ "${fixture}" ];
    services.bcenv.agentCommand = [
      "${pkgs.writeShellScript "fixture-agent" ''
        set -eu
        bcenv-workspace --repository /etc/bcenv/fixture \
          --revision "$(cat /etc/bcenv/fixture/revision)"
        echo started >> /var/lib/bcenv/agent-starts
        exec sleep infinity
      ''}"
    ];
    virtualisation.memorySize = 1024;
  };
in
pkgs.testers.runNixOSTest {
  name = "bcenv-roles";
  nodes = {
    supervisor = { ... }: {
      imports = [ ../modules/supervisor.nix ];
      virtualisation.memorySize = 1024;
    };
    alice = competitor;
    bob = competitor;
  };
  testScript = ''
    alice.start(allow_reboot=True)
    bob.start()
    supervisor.start()
    for machine, role in [(supervisor, "supervisor"), (alice, "competitor"), (bob, "competitor")]:
        machine.wait_for_unit("bcenv-image-check.service")
        machine.succeed(f"bcenv-doctor | grep 'BCENV_IMAGE_READY role={role}'")
        machine.fail("su -s /bin/sh bcenv -c 'touch /etc/bcenv/forbidden'")
        machine.fail("su -s /bin/sh bcenv -c 'sudo -n true'")
        machine.succeed("test ! -S /var/run/docker.sock")
        ssh_config = machine.succeed("sshd -T -f /etc/ssh/sshd_config")
        assert "passwordauthentication no" in ssh_config.lower().splitlines(), ssh_config
    supervisor.fail("systemctl is-active bcenv-agent.service")
    for machine in [alice, bob]:
        machine.wait_for_unit("bcenv-agent.service")
        machine.wait_until_succeeds("test -f /var/lib/bcenv/workspace/bot.txt")
    alice.succeed("su -s /bin/sh bcenv -c 'echo private > /var/lib/bcenv/workspace/only-alice'")
    bob.succeed("test ! -e /var/lib/bcenv/workspace/only-alice")
    alice.succeed("systemctl kill --signal=SIGKILL bcenv-agent.service")
    alice.wait_until_succeeds("test $(wc -l < /var/lib/bcenv/agent-starts) -ge 2")
    alice.succeed("grep private /var/lib/bcenv/workspace/only-alice")
    alice.reboot()
    alice.wait_for_unit("bcenv-agent.service")
    alice.wait_until_succeeds("test $(wc -l < /var/lib/bcenv/agent-starts) -ge 3")
    alice.succeed("grep private /var/lib/bcenv/workspace/only-alice")
    bob.succeed("test $(wc -l < /var/lib/bcenv/agent-starts) -eq 1")
  '';
}
