# Boot the actual GCE disk under QEMU/KVM and require its role readiness marker.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'usage: bcenv-test-gce-image <image-output-directory> <supervisor|competitor>' >&2
  exit 2
fi
image_dir=$1
role=$2
case "$role" in supervisor|competitor) ;; *) exit 2 ;; esac
test -r /dev/kvm && test -w /dev/kvm || { echo 'KVM access required' >&2; exit 1; }
archives=("$image_dir"/*.raw.tar.gz)
test "${#archives[@]}" -eq 1 && test -f "${archives[0]}"
test "$(tar -tzf "${archives[0]}")" = disk.raw
scratch=$(mktemp -d)
qemu_pid=''
cleanup() {
  if [ -n "$qemu_pid" ]; then
    kill "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT
tar -xzf "${archives[0]}" -C "$scratch"
log="${BCENV_BOOT_LOG:-$PWD/boot-$role.log}"
qemu-system-x86_64 -enable-kvm -m 2048 -smp 2 \
  -drive "file=$scratch/disk.raw,format=raw,if=virtio" -snapshot \
  -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
  -display none -serial stdio -monitor none -no-reboot >"$log" 2>&1 &
qemu_pid=$!
for _ in $(seq 1 180); do
  if grep -q "BCENV_IMAGE_READY role=$role" "$log"; then
    echo "GCE disk boot passed: $role"
    exit 0
  fi
  if ! kill -0 "$qemu_pid" 2>/dev/null; then break; fi
  sleep 2
done
echo "GCE disk did not become ready; see $log" >&2
tail -80 "$log" >&2
exit 1
