param(
    [Parameter(Mandatory = $true)][string]$UnsignedPath,
    [Parameter(Mandatory = $true)][string]$SignedPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Read structured-storage streams without installing or executing the MSI.
# Authenticode changes the container allocation and these two root streams;
# all database tables, custom actions, summary information and cabinets must
# remain byte-identical. Do not exempt signature-looking nested content.
$source = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;

namespace MarketSentinelSigning {
    [ComImport, Guid("0000000b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IStorage {
        void CreateStream([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint reserved1, uint reserved2, out IStream stream);
        void OpenStream([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr reserved1, uint mode, uint reserved2, out IStream stream);
        void CreateStorage([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint reserved1, uint reserved2, out IStorage storage);
        void OpenStorage([MarshalAs(UnmanagedType.LPWStr)] string name, IStorage priority, uint mode, IntPtr exclude, uint reserved, out IStorage storage);
        void CopyTo(uint count, IntPtr ids, IntPtr exclude, IStorage destination);
        void MoveElementTo([MarshalAs(UnmanagedType.LPWStr)] string name, IStorage destination, [MarshalAs(UnmanagedType.LPWStr)] string newName, uint flags);
        void Commit(uint flags);
        void Revert();
        void EnumElements(uint reserved1, IntPtr reserved2, uint reserved3, out IEnumStorage enumerator);
        void DestroyElement([MarshalAs(UnmanagedType.LPWStr)] string name);
        void RenameElement([MarshalAs(UnmanagedType.LPWStr)] string oldName, [MarshalAs(UnmanagedType.LPWStr)] string newName);
        void SetElementTimes([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr creation, IntPtr access, IntPtr modification);
        void SetClass(ref Guid clsid);
        void SetStateBits(uint bits, uint mask);
        void Stat(out STATSTG result, uint flags);
    }
    [ComImport, Guid("0000000d-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IEnumStorage {
        [PreserveSig] int Next(uint count, [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex=0)] STATSTG[] result, out uint fetched);
        void Skip(uint count);
        void Reset();
        void Clone(out IEnumStorage enumerator);
    }
    public static class PayloadVerifier {
        [DllImport("ole32.dll", CharSet=CharSet.Unicode, PreserveSig=false)]
        static extern void StgOpenStorage(string path, IStorage priority, uint mode, IntPtr exclude, uint reserved, out IStorage storage);

        static Dictionary<string,string> Snapshot(string path) {
            var result = new Dictionary<string,string>(StringComparer.Ordinal);
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            IStorage storage = null;
            IEnumStorage enumerator = null;
            IntPtr readCount = Marshal.AllocCoTaskMem(4);
            long total = 0;
            int entries = 0;
            try {
                StgOpenStorage(path, null, 0x20, IntPtr.Zero, 0, out storage);
                STATSTG root;
                storage.Stat(out root, 1);
                result.Add("root-clsid", root.clsid.ToString());
                result.Add("root-state", root.grfStateBits.ToString());
                storage.EnumElements(0, IntPtr.Zero, 0, out enumerator);
                var item = new STATSTG[1];
                uint fetched;
                while (true) {
                    int status = enumerator.Next(1, item, out fetched);
                    if (status == 1 && fetched == 0) break;
                    if (status != 0 || fetched != 1 || ++entries > 4096) throw new InvalidOperationException("Invalid storage inventory.");
                    var entry = item[0];
                    if (String.IsNullOrEmpty(entry.pwcsName) || !names.Add(entry.pwcsName) || entry.type != 2) throw new InvalidOperationException("MSI must contain only unique root streams.");
                    if (entry.cbSize < 0 || entry.cbSize > 268435456 || (total += entry.cbSize) > 268435456) throw new InvalidOperationException("MSI stream budget exceeded.");
                    if (entry.pwcsName == "\u0005DigitalSignature" || entry.pwcsName == "\u0005MsiDigitalSignatureEx") continue;
                    IStream stream = null;
                    try {
                        storage.OpenStream(entry.pwcsName, IntPtr.Zero, 0x10, 0, out stream);
                        using (var hash = SHA256.Create()) {
                            var buffer = new byte[65536];
                            long consumed = 0;
                            while (true) {
                                Marshal.WriteInt32(readCount, 0);
                                stream.Read(buffer, buffer.Length, readCount);
                                int count = Marshal.ReadInt32(readCount);
                                if (count < 0 || count > buffer.Length || (consumed += count) > entry.cbSize) throw new InvalidOperationException("Invalid stream size.");
                                if (count == 0) break;
                                hash.TransformBlock(buffer, 0, count, buffer, 0);
                            }
                            if (consumed != entry.cbSize) throw new InvalidOperationException("Truncated MSI stream.");
                            hash.TransformFinalBlock(new byte[0], 0, 0);
                            result.Add("stream:" + entry.pwcsName, entry.cbSize + ":" + Convert.ToBase64String(hash.Hash));
                        }
                    } finally { if (stream != null) Marshal.FinalReleaseComObject(stream); }
                }
                if (entries == 0) throw new InvalidOperationException("Empty MSI storage.");
                return result;
            } finally {
                Marshal.FreeCoTaskMem(readCount);
                if (enumerator != null) Marshal.FinalReleaseComObject(enumerator);
                if (storage != null) Marshal.FinalReleaseComObject(storage);
            }
        }
        public static void AssertEqual(string unsignedPath, string signedPath) {
            var before = Snapshot(unsignedPath);
            var after = Snapshot(signedPath);
            if (before.Count != after.Count) throw new InvalidOperationException("MSI stream inventory changed during signing.");
            foreach (var entry in before) {
                string value;
                if (!after.TryGetValue(entry.Key, out value) || value != entry.Value) throw new InvalidOperationException("MSI payload or metadata changed during signing.");
            }
        }
    }
}
'@

Add-Type -TypeDefinition $source
$unsigned = (Resolve-Path -LiteralPath $UnsignedPath -ErrorAction Stop).ProviderPath
$signed = (Resolve-Path -LiteralPath $SignedPath -ErrorAction Stop).ProviderPath
[MarketSentinelSigning.PayloadVerifier]::AssertEqual($unsigned, $signed)
Write-Output "[ok] MSI database, metadata, actions and cabinet streams survived signing unchanged."
