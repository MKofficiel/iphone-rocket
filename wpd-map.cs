// Windows' view of the iPhone, through the Windows Portable Devices API.
//
// List (read-only):   wpd-map.exe 202411 202410
//   Output: folder <tab> name <tab> size <tab> ISO date <tab> object id
//   Windows renames many files, so rocket.py needs this to match them by exact size.
//
// Verify one object:  wpd-map.exe verify <id> <name> <size> <sha256 or ->
// Delete one object:  wpd-map.exe delete <id> <name> <size>
//   Always two separate runs: the iPhone answers "busy" (0x800700AA) while a process
//   still holds the stream used to verify the content. The iPhone handles the
//   deletion itself, which keeps its Photos library consistent.
//   Exit codes: 0 done, 3 no iPhone, 10 not the expected object, 11 content differs, 12 refused.
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Text;

[StructLayout(LayoutKind.Sequential)]
public struct PKEY { public Guid fmtid; public uint pid; public PKEY(string g, uint p) { fmtid = new Guid(g); pid = p; } }

[StructLayout(LayoutKind.Sequential, Size = 24)]
public struct PV { public ushort vt; public ushort a, b, c; public long v1; public long v2; }

[ComImport, Guid("a1567595-4c2f-4574-a6fa-ecef917b9a40"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IManager {
    void GetDevices([In, Out, MarshalAs(UnmanagedType.LPArray)] IntPtr[] ids, ref uint count);
}
[ComImport, Guid("dada2357-e0ad-492e-98db-dd61c53ba353"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IKeys { void _GetCount(); void _GetAt(); void Add(ref PKEY key); }
[ComImport, Guid("89b2e422-4f1b-4316-bcef-a44afea83eb3"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IObjectIds { void GetCount(ref uint count); void GetAt(uint index, ref PV value); void Add(ref PV value); }
[ComImport, Guid("6848f6f2-3155-4f86-b6f5-263eeeab3143"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IValues {
    void _GetCount(); void _GetAt(); void _SetValue();
    [PreserveSig] int GetValue(ref PKEY key, out PV value);
    void _SetString();
    [PreserveSig] int GetStringValue(ref PKEY key, [MarshalAs(UnmanagedType.LPWStr)] out string value);
    void _SetUInt(); void _GetUInt(); void _SetInt(); void _GetInt(); void _SetUInt8();
    [PreserveSig] int GetUnsignedLargeIntegerValue(ref PKEY key, out ulong value);
}
[ComImport, Guid("10ece955-cf41-4728-bfa0-41eedf1bbf19"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IEnumIds { [PreserveSig] int Next(uint count, [In, Out, MarshalAs(UnmanagedType.LPArray)] IntPtr[] ids, ref uint fetched); }
[ComImport, Guid("7f6d695c-03df-4439-a809-59266beee3a6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IProps {
    void _GetSupported(); void _GetAttrs();
    void GetValues([MarshalAs(UnmanagedType.LPWStr)] string id, IKeys keys, out IValues values);
}
[ComImport, Guid("fd8878ac-d841-4d17-891c-e6829cdb6934"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IResources {
    void _GetSupported(); void _GetAttrs();
    void GetStream([MarshalAs(UnmanagedType.LPWStr)] string id, ref PKEY key, uint mode, ref uint optimalBuffer, out IStream stream);
}
[ComImport, Guid("6a96ed84-7c73-4480-9938-bf5af477d426"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IContent {
    void EnumObjects(uint flags, [MarshalAs(UnmanagedType.LPWStr)] string parent, IntPtr filter, out IEnumIds ids);
    void Properties(out IProps props);
    void Transfer(out IResources resources);
    void _CreateWithProperties(); void _CreateWithData();
    [PreserveSig] int Delete(uint options, IObjectIds ids, ref IntPtr results);
}
[ComImport, Guid("625e2df8-6392-4cf0-9ad1-3cfa5f17775c"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IDevice {
    void Open([MarshalAs(UnmanagedType.LPWStr)] string id, IValues client);
    void _SendCommand();
    void Content(out IContent content);
    void _Capabilities(); void _Cancel();
    void Close();
}
[ComImport, Guid("0af10cec-2ecd-4b92-9581-34f6ae0637f3")] public class ManagerClass { }
[ComImport, Guid("f7c0039a-4762-488a-b4b3-760ef9a1ba9b")] public class DeviceClass { }
[ComImport, Guid("0c15d503-d017-47ce-9016-7b3f978721cc")] public class ValuesClass { }
[ComImport, Guid("de2d022d-2480-43be-97f0-d1fa2cf98f4f")] public class KeysClass { }
[ComImport, Guid("08a99e2f-6d6d-4b80-af5a-baf2bcbe4cb9")] public class ObjectIdsClass { }

public static class WpdMap {
    const string OBJ = "EF6B490D-5CD8-437A-AFFC-DA8B60EE4A3C";
    const string RESOURCE = "E81E79BE-34F0-41BF-B53F-F1A06AE87842";
    const ushort VT_DATE = 7, VT_LPWSTR = 31;
    const int NO_IPHONE = 3, WRONG_OBJECT = 10, WRONG_CONTENT = 11, REFUSED = 12;
    static PKEY ORIGINAL_NAME = new PKEY(OBJ, 12), SIZE = new PKEY(OBJ, 11), CREATED = new PKEY(OBJ, 18);

    static List<string> Children(IContent content, string parent) {
        IEnumIds e;
        content.EnumObjects(0, parent, IntPtr.Zero, out e);
        var list = new List<string>();
        while (true) {
            var ids = new IntPtr[64];
            uint got = 0;
            int hr = e.Next(64, ids, ref got);
            for (int i = 0; i < got; i++) {
                list.Add(Marshal.PtrToStringUni(ids[i]));
                Marshal.FreeCoTaskMem(ids[i]);
            }
            if (got == 0 || hr != 0) return list;
        }
    }

    static string Name(IValues values) {
        string name;
        var key = ORIGINAL_NAME;
        return values.GetStringValue(ref key, out name) == 0 ? name : "";
    }

    static ulong Size(IValues values) {
        ulong size = 0;
        var key = SIZE;
        values.GetUnsignedLargeIntegerValue(ref key, out size);
        return size;
    }

    static string Created(IValues values) {
        PV value;
        var key = CREATED;
        if (values.GetValue(ref key, out value) != 0 || value.vt != VT_DATE) return "";
        return DateTime.FromOADate(BitConverter.Int64BitsToDouble(value.v1)).ToString("s");
    }

    static void List(IContent content, IProps props, IKeys keys, string[] prefixes, TextWriter output) {
        foreach (string storage in Children(content, "DEVICE")) {
            foreach (string folder in Children(content, storage)) {
                IValues folderValues;
                props.GetValues(folder, keys, out folderValues);
                string folderName = Name(folderValues);
                bool wanted = false;
                foreach (string prefix in prefixes) wanted |= folderName.StartsWith(prefix, StringComparison.Ordinal);
                if (!wanted) continue;
                foreach (string file in Children(content, folder)) {
                    IValues values;
                    props.GetValues(file, keys, out values);
                    output.WriteLine(folderName + "\t" + Name(values) + "\t" + Size(values) + "\t" + Created(values) + "\t" + file);
                }
            }
        }
    }

    static string Sha256(IContent content, string id) {
        IResources resources;
        content.Transfer(out resources);
        var key = new PKEY(RESOURCE, 0);
        uint optimal = 256 * 1024;
        IStream stream;
        resources.GetStream(id, ref key, 0, ref optimal, out stream);
        var buffer = new byte[Math.Max(optimal, 64 * 1024)];
        IntPtr readPointer = Marshal.AllocCoTaskMem(4);
        try {
            using (var hasher = SHA256.Create()) {
                while (true) {
                    stream.Read(buffer, buffer.Length, readPointer);
                    int read = Marshal.ReadInt32(readPointer);
                    if (read <= 0) break;
                    hasher.TransformBlock(buffer, 0, read, null, 0);
                }
                hasher.TransformFinalBlock(new byte[0], 0, 0);
                return BitConverter.ToString(hasher.Hash).Replace("-", "").ToLowerInvariant();
            }
        } finally {
            Marshal.FreeCoTaskMem(readPointer);
        }
    }

    static bool IsExpected(IProps props, IKeys keys, string id, string name, ulong size) {
        IValues values;
        try {
            props.GetValues(id, keys, out values);
        } catch (COMException) {
            return false;
        }
        return Name(values) == name && Size(values) == size;
    }

    static int Verify(IContent content, IProps props, IKeys keys, string[] args) {
        if (!IsExpected(props, keys, args[1], args[2], ulong.Parse(args[3]))) return WRONG_OBJECT;
        if (args[4] != "-" && Sha256(content, args[1]) != args[4]) return WRONG_CONTENT;
        return 0;
    }

    static int Delete(IContent content, IProps props, IKeys keys, string[] args, TextWriter output) {
        string id = args[1];
        if (!IsExpected(props, keys, id, args[2], ulong.Parse(args[3]))) return WRONG_OBJECT;
        var ids = (IObjectIds)new ObjectIdsClass();
        IntPtr text = Marshal.StringToCoTaskMemUni(id);
        try {
            var value = new PV { vt = VT_LPWSTR, v1 = text.ToInt64() };
            ids.Add(ref value);
            // The per-object result carries the real reason: hr alone only says "not everything".
            IntPtr raw = IntPtr.Zero;
            int hr = content.Delete(0, ids, ref raw);
            int itemCode = ItemResult(raw);
            if (hr == 0 && itemCode == 0) return 0;
            output.WriteLine("0x" + hr.ToString("X8") + " 0x" + itemCode.ToString("X8"));
            return REFUSED;
        } finally {
            Marshal.FreeCoTaskMem(text);
        }
    }

    static int ItemResult(IntPtr raw) {
        if (raw == IntPtr.Zero) return 0;
        var results = (IObjectIds)Marshal.GetObjectForIUnknown(raw);
        Marshal.Release(raw);
        uint count = 0;
        results.GetCount(ref count);
        if (count == 0) return 0;
        var value = new PV();
        results.GetAt(0, ref value);
        return (int)value.v1;
    }

    [MTAThread]
    static int Main(string[] args) {
        var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false));
        var manager = (IManager)new ManagerClass();
        uint count = 0;
        manager.GetDevices(null, ref count);
        var devices = new IntPtr[count];
        manager.GetDevices(devices, ref count);
        for (int d = 0; d < count; d++) {
            string id = Marshal.PtrToStringUni(devices[d]);
            if (id.IndexOf("vid_05ac", StringComparison.OrdinalIgnoreCase) < 0) continue;
            var device = (IDevice)new DeviceClass();
            device.Open(id, (IValues)new ValuesClass());
            IContent content;
            device.Content(out content);
            IProps props;
            content.Properties(out props);
            var keys = (IKeys)new KeysClass();
            foreach (var k in new[] { ORIGINAL_NAME, SIZE, CREATED }) { var key = k; keys.Add(ref key); }
            int code = 0;
            if (args.Length == 5 && args[0] == "verify") code = Verify(content, props, keys, args);
            else if (args.Length == 4 && args[0] == "delete") code = Delete(content, props, keys, args, output);
            else List(content, props, keys, args, output);
            device.Close();
            output.Flush();
            return code;
        }
        return NO_IPHONE;
    }
}
