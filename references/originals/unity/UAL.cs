#if UNITY_EDITOR
#pragma warning disable UDR0001
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class UAL
{
    private const int IdleThresholdSeconds = 300;
    private const int SnapshotIntervalSeconds = 300;
    private const int WorkWindowSeconds = 32400;
    private const string PublicModulusBase64 = "zdZXic+1Gd5TzbjYZgEalJVzSHrvRyjqTlnAmuqMAKuR3Zh0ZqkYAt0hQx9xCyPop0it80TJMaXSGelYjjhLdAy/OxWOwkxM0FYcROQ5/dTGqkfBXyZhx+MGSMnN5PX4vcKBqGau2mHuxrWSBKPFmxngRohadNp09pkA6GWIJzB43vBD2fj/eLfeqWgO9OHVyNBlnugbdtgAj+u4bzkMm2XPtLhrwzSMoIqXOnDqk3nJx2WdlmLNAI7OQJShUdd4SQNeTmwdr05COrEffX1bYhAlJr6fnmfCkY+HYtOo+0ak1CF/9wD8n8SQEhar68EUrGT8v+LH6cx14C502D8wlw==";
    private const string PublicExponentBase64 = "AQAB";

    private static string _sessionId;
    private static string _projectRoot;
    private static string _author;
    private static string _logPath;

    private static DateTime _currentDay;
    private static DateTime _firstActivity;
    private static DateTime _lastActivity;
    private static DateTime _lastAccountingTime;
    private static DateTime _lastSnapshotTime;
    private static double _activeSeconds;
    private static double _idleSeconds;
    private static double _overtimeActiveSeconds;
    private static bool _hasActivity;
    private static bool _dirty;
    private static bool _initialized;

    [InitializeOnLoadMethod]
    private static void Initialize()
    {
        if (_initialized)
        {
            return;
        }

        _initialized = true;
        _sessionId = Guid.NewGuid().ToString("N");
        _projectRoot = Directory.GetParent(UnityEngine.Application.dataPath).FullName;
        _author = ResolveAuthor();
        _logPath = Path.Combine(_projectRoot, "Assets", "Plugins", "UAL", "UAL" + ResolveInitials(_author) + ".json");
        ResetDay(DateTime.Now.Date);

        Selection.selectionChanged += RecordActivity;
        Undo.undoRedoPerformed += RecordActivity;
        EditorApplication.playModeStateChanged += OnPlayModeStateChanged;
        EditorSceneManager.sceneSaved += OnSceneSaved;
        AssemblyReloadEvents.beforeAssemblyReload += Flush;
        EditorApplication.quitting += Flush;
        EditorApplication.update += Update;
    }

    public static void RecordExternalActivity()
    {
        EnsureInitialized();
        RecordActivity();
    }

    private static void EnsureInitialized()
    {
        if (!_initialized)
        {
            Initialize();
        }
    }

    private static void Update()
    {
        DateTime now = DateTime.Now;

        if (_hasActivity && !File.Exists(_logPath))
        {
            ResetDay(now.Date);
            return;
        }

        AccumulateIdleTo(now);

        if (now.Date != _currentDay)
        {
            Flush();
            ResetDay(now.Date);
        }

        if (_hasActivity && (now - _lastSnapshotTime).TotalSeconds >= SnapshotIntervalSeconds)
        {
            WriteSnapshot(now);
        }
    }

    private static void RecordActivity()
    {
        bool logFileExists = File.Exists(_logPath);

        if (_hasActivity && !logFileExists)
        {
            ResetDay(DateTime.Now.Date);
        }

        DateTime now = DateTime.Now;

        if (now.Date != _currentDay)
        {
            Flush();
            ResetDay(now.Date);
        }

        if (!_hasActivity)
        {
            _firstActivity = now;
            _lastAccountingTime = now;
            _hasActivity = true;
        }
        else
        {
            AccumulateActivityTo(now);
        }

        _lastActivity = now;
        _dirty = true;

        if (!logFileExists || (now - _lastSnapshotTime).TotalSeconds >= 30)
        {
            WriteSnapshot(now);
        }
    }

    private static void OnPlayModeStateChanged(PlayModeStateChange state)
    {
        RecordActivity();
    }

    private static void OnSceneSaved(UnityEngine.SceneManagement.Scene scene)
    {
        RecordActivity();
    }

    private static void Flush()
    {
        if (!_hasActivity)
        {
            return;
        }

        if (!File.Exists(_logPath))
        {
            ResetDay(DateTime.Now.Date);
            return;
        }

        DateTime now = DateTime.Now;
        AccumulateIdleTo(now);
        WriteSnapshot(now);
    }

    private static void ResetDay(DateTime day)
    {
        _currentDay = day;
        _firstActivity = DateTime.MinValue;
        _lastActivity = DateTime.MinValue;
        _lastAccountingTime = DateTime.Now;
        _lastSnapshotTime = DateTime.MinValue;
        _activeSeconds = 0;
        _idleSeconds = 0;
        _overtimeActiveSeconds = 0;
        _hasActivity = false;
        _dirty = false;
    }

    private static void AccumulateActivityTo(DateTime now)
    {
        if (!_hasActivity || now <= _lastActivity)
        {
            return;
        }

        double elapsedSinceLastActivity = (now - _lastActivity).TotalSeconds;
        double elapsedSinceLastAccounting = (now - _lastAccountingTime).TotalSeconds;

        if (elapsedSinceLastActivity <= IdleThresholdSeconds)
        {
            AccumulateTrackedSeconds(_lastAccountingTime, now, true);
        }
        else
        {
            AccumulateTrackedSeconds(_lastAccountingTime, now, false);
        }

        _lastAccountingTime = now;
        _dirty = true;
    }

    private static void AccumulateIdleTo(DateTime now)
    {
        if (!_hasActivity || now <= _lastAccountingTime)
        {
            return;
        }

        if ((now - _lastActivity).TotalSeconds <= IdleThresholdSeconds)
        {
            return;
        }

        AccumulateTrackedSeconds(_lastAccountingTime, now, false);
        _lastAccountingTime = now;
        _dirty = true;
    }

    private static void AccumulateTrackedSeconds(DateTime start, DateTime end, bool isActive)
    {
        if (end <= start)
        {
            return;
        }

        DateTime workWindowEnd = _firstActivity.AddSeconds(WorkWindowSeconds);

        if (start < workWindowEnd)
        {
            DateTime normalEnd = end;

            if (normalEnd > workWindowEnd)
            {
                normalEnd = workWindowEnd;
            }

            if (normalEnd > start)
            {
                if (isActive)
                {
                    _activeSeconds += (normalEnd - start).TotalSeconds;
                }
                else
                {
                    _idleSeconds += (normalEnd - start).TotalSeconds;
                }
            }
        }

        if (isActive && end > workWindowEnd)
        {
            DateTime overtimeStart = start;

            if (overtimeStart < workWindowEnd)
            {
                overtimeStart = workWindowEnd;
            }

            if (end > overtimeStart)
            {
                _overtimeActiveSeconds += (end - overtimeStart).TotalSeconds;
            }
        }
    }

    private static void WriteSnapshot(DateTime recordedAt)
    {
        if (!_dirty || !_hasActivity)
        {
            return;
        }

        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_logPath));

            UALSnapshot snapshot = new UALSnapshot();
            snapshot.author = _author;
            snapshot.sessionId = _sessionId;
            snapshot.date = _currentDay.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            snapshot.firstActivity = _firstActivity.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
            snapshot.lastActivity = _lastActivity.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
            snapshot.activeSeconds = (long)Math.Round(_activeSeconds);
            snapshot.idleSeconds = (long)Math.Round(_idleSeconds);
            snapshot.overtimeActiveSeconds = (long)Math.Round(_overtimeActiveSeconds);
            snapshot.recordedAt = recordedAt.ToString("o", CultureInfo.InvariantCulture);
            snapshot.idleThresholdSeconds = IdleThresholdSeconds;
            snapshot.workWindowSeconds = WorkWindowSeconds;

            string json = JsonUtility.ToJson(snapshot);
            string encryptedLine = EncryptSnapshot(json);

            File.AppendAllText(_logPath, encryptedLine + Environment.NewLine);
            AssetDatabase.ImportAsset(ToAssetPath(_logPath), ImportAssetOptions.ForceSynchronousImport);
            _lastSnapshotTime = recordedAt;
            _dirty = false;
        }
        catch (Exception exception)
        {
            _lastSnapshotTime = recordedAt;
            UnityEngine.Debug.LogWarning("UAL failed to write encrypted activity snapshot: " + exception.Message);
        }
    }

    private static string EncryptSnapshot(string json)
    {
        byte[] plainBytes = Encoding.UTF8.GetBytes(json);
        byte[] aesKey = RandomBytes(32);
        byte[] hmacKey = RandomBytes(32);
        byte[] iv = RandomBytes(16);
        byte[] cipherBytes;

        using (Aes aes = Aes.Create())
        {
            aes.KeySize = 256;
            aes.BlockSize = 128;
            aes.Mode = CipherMode.CBC;
            aes.Padding = PaddingMode.PKCS7;
            aes.Key = aesKey;
            aes.IV = iv;

            using (ICryptoTransform encryptor = aes.CreateEncryptor())
            {
                cipherBytes = encryptor.TransformFinalBlock(plainBytes, 0, plainBytes.Length);
            }
        }

        byte[] keyMaterial = Combine(aesKey, hmacKey);
        byte[] encryptedKey;

        using (RSACryptoServiceProvider rsa = new RSACryptoServiceProvider(2048))
        {
            RSAParameters parameters = new RSAParameters();
            parameters.Modulus = Convert.FromBase64String(PublicModulusBase64);
            parameters.Exponent = Convert.FromBase64String(PublicExponentBase64);
            rsa.ImportParameters(parameters);
            encryptedKey = rsa.Encrypt(keyMaterial, false);
        }

        byte[] unsignedPacket = BuildPacket(encryptedKey, iv, cipherBytes, null);
        byte[] signature;

        using (HMACSHA256 hmac = new HMACSHA256(hmacKey))
        {
            signature = hmac.ComputeHash(unsignedPacket);
        }

        byte[] signedPacket = BuildPacket(encryptedKey, iv, cipherBytes, signature);
        return Convert.ToBase64String(signedPacket);
    }

    private static byte[] BuildPacket(byte[] encryptedKey, byte[] iv, byte[] cipherBytes, byte[] signature)
    {
        using (MemoryStream stream = new MemoryStream())
        {
            WriteAscii(stream, "UAL1");
            WriteInt(stream, encryptedKey.Length);
            stream.Write(encryptedKey, 0, encryptedKey.Length);
            WriteInt(stream, iv.Length);
            stream.Write(iv, 0, iv.Length);
            WriteInt(stream, cipherBytes.Length);
            stream.Write(cipherBytes, 0, cipherBytes.Length);

            if (signature != null)
            {
                WriteInt(stream, signature.Length);
                stream.Write(signature, 0, signature.Length);
            }

            return stream.ToArray();
        }
    }

    private static byte[] RandomBytes(int count)
    {
        byte[] bytes = new byte[count];

        using (RandomNumberGenerator generator = RandomNumberGenerator.Create())
        {
            generator.GetBytes(bytes);
        }

        return bytes;
    }

    private static byte[] Combine(byte[] first, byte[] second)
    {
        byte[] combined = new byte[first.Length + second.Length];
        Buffer.BlockCopy(first, 0, combined, 0, first.Length);
        Buffer.BlockCopy(second, 0, combined, first.Length, second.Length);
        return combined;
    }

    private static void WriteAscii(Stream stream, string value)
    {
        byte[] bytes = Encoding.ASCII.GetBytes(value);
        stream.Write(bytes, 0, bytes.Length);
    }

    private static void WriteInt(Stream stream, int value)
    {
        byte[] bytes = BitConverter.GetBytes(value);

        if (!BitConverter.IsLittleEndian)
        {
            Array.Reverse(bytes);
        }

        stream.Write(bytes, 0, bytes.Length);
    }

    private static string ResolveAuthor()
    {
        string author = RunGitConfig("user.name");

        if (string.IsNullOrEmpty(author))
        {
            author = Environment.UserName;
        }

        if (string.IsNullOrEmpty(author))
        {
            author = "Unknown User";
        }

        return author.Trim();
    }

    private static string RunGitConfig(string key)
    {
        try
        {
            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = "git";
            startInfo.Arguments = "config " + key;
            startInfo.WorkingDirectory = _projectRoot;
            startInfo.RedirectStandardOutput = true;
            startInfo.RedirectStandardError = true;
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;

            using (Process process = new Process())
            {
                process.StartInfo = startInfo;
                process.Start();
                string output = process.StandardOutput.ReadToEnd().Trim();
                process.WaitForExit();

                if (process.ExitCode == 0)
                {
                    return output;
                }
            }
        }
        catch (Exception)
        {
        }

        return string.Empty;
    }

    private static string ResolveInitials(string author)
    {
        List<string> parts = new List<string>();
        MatchCollection matches = Regex.Matches(author.ToLowerInvariant(), "[a-z0-9]+");

        foreach (Match match in matches)
        {
            if (!string.IsNullOrEmpty(match.Value))
            {
                parts.Add(match.Value);
            }
        }

        if (parts.Count == 0)
        {
            return "unknown";
        }

        StringBuilder builder = new StringBuilder();

        foreach (string part in parts)
        {
            builder.Append(part[0]);
        }

        return builder.ToString();
    }

    private static string ToAssetPath(string path)
    {
        string normalizedPath = path.Replace("\\", "/");
        string normalizedRoot = _projectRoot.Replace("\\", "/");

        if (normalizedPath.StartsWith(normalizedRoot + "/", StringComparison.OrdinalIgnoreCase))
        {
            return normalizedPath.Substring(normalizedRoot.Length + 1);
        }

        return normalizedPath;
    }

    [Serializable]
    private class UALSnapshot
    {
        public string author;
        public string sessionId;
        public string date;
        public string firstActivity;
        public string lastActivity;
        public long activeSeconds;
        public long idleSeconds;
        public long overtimeActiveSeconds;
        public string recordedAt;
        public int idleThresholdSeconds;
        public int workWindowSeconds;
    }
}

public sealed class UALAssetPostprocessor : AssetPostprocessor
{
    private static void OnPostprocessAllAssets(
        string[] importedAssets,
        string[] deletedAssets,
        string[] movedAssets,
        string[] movedFromAssetPaths)
    {
        if (HasExternalEntries(importedAssets) || HasExternalEntries(deletedAssets) || HasExternalEntries(movedAssets) || HasExternalEntries(movedFromAssetPaths))
        {
            UAL.RecordExternalActivity();
        }
    }

    private static bool HasExternalEntries(string[] values)
    {
        if (values == null)
        {
            return false;
        }

        foreach (string value in values)
        {
            if (!IsUALLogAsset(value))
            {
                return true;
            }
        }

        return false;
    }

    private static bool IsUALLogAsset(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return false;
        }

        string normalizedValue = value.Replace("\\", "/");

        if (!normalizedValue.StartsWith("Assets/Plugins/UAL/", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        string fileName = Path.GetFileName(normalizedValue);

        if (fileName.StartsWith("UAL", StringComparison.OrdinalIgnoreCase) && (fileName.EndsWith(".json", StringComparison.OrdinalIgnoreCase) || fileName.EndsWith(".json.meta", StringComparison.OrdinalIgnoreCase)))
        {
            return true;
        }

        return false;
    }
}
#endif
