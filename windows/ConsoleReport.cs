using System;
using System.Runtime.InteropServices;

internal static class ConsoleReport
{
    [DllImport("kernel32.dll")] private static extern bool FreeConsole();
    [DllImport("kernel32.dll")] private static extern bool AttachConsole(uint processId);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll")] private static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll")] private static extern bool SetConsoleMode(IntPtr handle, uint mode);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern bool WriteConsole(IntPtr handle, string text, uint length, out uint written, IntPtr reserved);
    [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);

    private static int Main(string[] args)
    {
        IntPtr handle = new IntPtr(-1);
        uint mode = 0;
        bool changed = false;
        try
        {
            uint parent;
            if (args.Length < 2 || !UInt32.TryParse(args[0], out parent)) return 1;
            string state = args[1];
            if (state != "idle" && state != "working" && state != "done" && state != "blocked" && state != "clear") return 1;
            string kind = args.Length > 2 ? args[2] : "";
            if (kind != "" && kind != "permission" && kind != "question") return 1;

            // Hook stdout is captured; only the launching process's console owns the report.
            FreeConsole();
            if (!AttachConsole(parent)) return 1;
            handle = CreateFile("CONOUT$", 0xC0000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
            if (handle == new IntPtr(-1) || !GetConsoleMode(handle, out mode)) return 1;
            if ((mode & 4) == 0)
            {
                if (!SetConsoleMode(handle, mode | 4)) return 1;
                changed = true;
            }
            string text = "\x1b]7501;state=" + state + ":app=copilot"
                + (kind == "" ? "" : ":kind=" + kind) + "\x1b\\";
            uint written;
            return WriteConsole(handle, text, (uint)text.Length, out written, IntPtr.Zero)
                && written == text.Length ? 0 : 1;
        }
        catch { return 1; }
        finally
        {
            if (handle != new IntPtr(-1))
            {
                if (changed) SetConsoleMode(handle, mode);
                CloseHandle(handle);
            }
            FreeConsole();
        }
    }
}
