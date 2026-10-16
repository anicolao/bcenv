{ runCommand, nodejs, fetchurl }:
let
  husky = fetchurl {
    url = "https://registry.npmjs.org/husky/-/husky-9.1.7.tgz";
    hash = "sha512-5gs5ytaNjBrh5Ow3zrvdUUY+0VxIuWVL4i9irt6friV+BqdCfmV11CQTWMiBYWHbXhco+J1kHfTOUkePhCDvMA==";
  };
in runCommand "bcenv-npm-cache" { nativeBuildInputs = [ nodejs ]; } ''
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  npm cache add ${husky} --cache "$out" --offline --ignore-scripts
''
