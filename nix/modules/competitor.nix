{ pkgs, ... }:
{
  imports = [ ./base.nix ];
  services.bcenv.role = "competitor";
  environment.systemPackages = [
    pkgs.jdk21
    pkgs.unzip
    pkgs.zip
  ];
  environment.variables.JAVA_HOME = "${pkgs.jdk21}";
}
