#!/usr/bin/env python3
"""从 Git 跟踪白名单生成可复现源码包与 Homebrew 配方，不打包运行数据。"""
import gzip
import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_check', ROOT / 'scripts/check-public-files.py')
checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)


def main():
    paths = checker.files()
    if not paths or checker.inspect(paths): raise SystemExit('请先通过公开文件检查。')
    version = (ROOT / 'VERSION').read_text().strip()
    target = ROOT / 'dist'; target.mkdir(exist_ok=True)
    asset = f'token-monitor-{version}.tar.gz'
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for relative in sorted(paths):
            if relative.parts[0] == 'Formula': continue
            body = (ROOT / relative).read_bytes()
            info = tarfile.TarInfo(f'token-monitor-{version}/{relative.as_posix()}')
            info.size = len(body); info.mode = 0o755 if relative.suffix == '.sh' else 0o644
            info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ''
            archive.addfile(info, io.BytesIO(body))
    with (target / asset).open('wb') as output:
        with gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed:
            compressed.write(raw.getvalue())
    digest = hashlib.sha256((target / asset).read_bytes()).hexdigest()
    (target / 'SHA256SUMS').write_text(f'{digest}  {asset}\n')
    formula = '''class TokenMonitor < Formula
  desc "Local token dashboard for Claude Code, Codex, WorkBuddy and Qianwen"
  homepage "https://github.com/spiritn/token-monitor"
  url "https://github.com/spiritn/token-monitor/releases/download/v__VERSION__/token-monitor-__VERSION__.tar.gz"
  sha256 "__DIGEST__"
  license "MIT"
  depends_on :macos
  depends_on "python@3.13"

  def install
    system "sh", "scripts/build-macos.sh"
    libexec.install "VERSION", "runtime.py", "cli.py", "daemon.py", "meter.py", "adapters.py", "web", "scripts", "native", ".build"
    (bin/"token-monitor").write <<~EOS
      #!/bin/sh
      exec "#{Formula["python@3.13"].opt_bin}/python3.13" "#{libexec}/cli.py" "$@"
    EOS
  end

  def caveats
    <<~EOS
      Start menu bar and collector, including login startup:
        token-monitor start
      Stop before uninstalling, keeping usage history:
        token-monitor uninstall
        brew uninstall token-monitor
      Data: ~/Library/Application Support/TokenMonitor
    EOS
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/token-monitor --version").strip
  end
end
'''.replace('__VERSION__', version).replace('__DIGEST__', digest)
    (ROOT / 'Formula/token-monitor.rb').write_text(formula)
    (target / 'token-monitor.rb').write_text(formula)
    print('已生成：', asset, 'SHA256SUMS', 'token-monitor.rb')


if __name__ == '__main__': main()
