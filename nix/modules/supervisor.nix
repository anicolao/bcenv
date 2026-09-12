{ pkgs, ... }:
{
  imports = [ ./base.nix ];
  services.bcenv.role = "supervisor";
  environment.systemPackages = [
    pkgs.google-cloud-sdk
    pkgs.openssh
  ];
}
