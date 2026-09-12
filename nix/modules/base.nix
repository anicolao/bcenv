{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.bcenv;
  workspaceTool = pkgs.writeScriptBin "bcenv-workspace" ''
    #!${pkgs.python3}/bin/python3
    ${builtins.readFile ../../scripts/workspace.py}
  '';
  doctor = pkgs.writeShellApplication {
    name = "bcenv-doctor";
    runtimeInputs = [
      pkgs.git
      pkgs.python3
      pkgs.nodejs_22
      pkgs.jq
      pkgs.curl
    ];
    text = ''
      test "$(cat /etc/bcenv/role)" = ${lib.escapeShellArg cfg.role}
      test -d /var/lib/bcenv
      git --version >/dev/null
      python3 --version >/dev/null
      node --version >/dev/null
      ${lib.optionalString (
        cfg.role == "competitor"
      ) "${pkgs.jdk21}/bin/java -version 2>&1 | grep -q '21'"}
      ${lib.optionalString (
        cfg.role == "supervisor"
      ) "${pkgs.google-cloud-sdk}/bin/gcloud --version >/dev/null"}
      echo 'BCENV_IMAGE_READY role=${cfg.role}'
    '';
  };
in
{
  options.services.bcenv = {
    role = lib.mkOption {
      type = lib.types.enum [
        "competitor"
        "supervisor"
      ];
    };
    agentCommand = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Agent executable and arguments. Empty leaves the agent service disabled.";
    };
  };
  config = {
    system.stateVersion = "26.05";
    nix.settings.experimental-features = [
      "nix-command"
      "flakes"
    ];
    # A worker can use Nix without becoming a trusted daemon user.
    nix.settings.trusted-users = [ "root" ];
    environment.systemPackages = [
      workspaceTool
      doctor
      pkgs.git
      pkgs.python3
      pkgs.nodejs_22
      pkgs.jq
      pkgs.curl
    ];
    environment.etc."bcenv/role".text = cfg.role + "\n";
    users.groups.bcenv = { };
    users.users.bcenv = {
      isNormalUser = true;
      group = "bcenv";
      home = "/var/lib/bcenv";
      createHome = true;
      homeMode = "0700";
    };
    services.openssh = {
      enable = true;
      settings = {
        PasswordAuthentication = false;
        KbdInteractiveAuthentication = false;
        PermitRootLogin = "no";
      };
    };
    networking.firewall.enable = true;
    security.sudo.wheelNeedsPassword = true;
    systemd.services.bcenv-image-check = {
      description = "Check bcenv image tools and report readiness on the serial console";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-tmpfiles-setup.service" ];
      serviceConfig = {
        Type = "oneshot";
        User = "bcenv";
        ExecStart = "${doctor}/bin/bcenv-doctor";
        RemainAfterExit = true;
        StandardOutput = "journal+console";
      };
    };
    systemd.services.bcenv-agent = lib.mkIf (cfg.agentCommand != [ ]) {
      description = "Configured bcenv ${cfg.role} agent";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network-online.target"
        "bcenv-image-check.service"
      ];
      wants = [ "network-online.target" ];
      requires = [ "bcenv-image-check.service" ];
      path = config.environment.systemPackages;
      serviceConfig = {
        User = "bcenv";
        Group = "bcenv";
        WorkingDirectory = "/var/lib/bcenv";
        ExecStart = lib.escapeShellArgs cfg.agentCommand;
        Restart = "on-failure";
        RestartSec = "5s";
        KillMode = "control-group";
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ "/var/lib/bcenv" ];
      };
    };
  };
}
