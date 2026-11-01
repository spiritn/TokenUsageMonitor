class TokenMonitor < Formula
  desc "Local token dashboard for Claude Code, Codex, WorkBuddy and Qianwen"
  homepage "https://github.com/spiritn/TokenUsageMonitor"
  url "https://github.com/spiritn/TokenUsageMonitor/releases/download/v0.1.0/token-monitor-0.1.0.tar.gz"
  sha256 "4e2ebd58296c3c0b7264e11951b8fc7ed445cabad15e76ede5ad6139308c6b82"
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
