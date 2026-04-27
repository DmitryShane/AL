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
using UnityEngine.Networking;

namespace AL.UAL
{
    public static class UAL
    {
        private const string Source = "ual";
        private const string PluginName = "UAL";
        private const string PluginVersion = "0.1.0";
        private const int IdleThresholdSeconds = 300;
        internal const int DefaultSendIntervalSeconds = 300;
        private const int WorkWindowSeconds = 32400;
        private const string PublicModulusBase64 = "zdZXic+1Gd5TzbjYZgEalJVzSHrvRyjqTlnAmuqMAKuR3Zh0ZqkYAt0hQx9xCyPop0it80TJMaXSGelYjjhLdAy/OxWOwkxM0FYcROQ5/dTGqkfBXyZhx+MGSMnN5PX4vcKBqGau2mHuxrWSBKPFmxngRohadNp09pkA6GWIJzB43vBD2fj/eLfeqWgO9OHVyNBlnugbdtgAj+u4bzkMm2XPtLhrwzSMoIqXOnDqk3nJx2WdlmLNAI7OQJShUdd4SQNeTmwdr05COrEffX1bYhAlJr6fnmfCkY+HYtOo+0ak1CF/9wD8n8SQEhar68EUrGT8v+LH6cx14C502D8wlw==";
        private const string PublicExponentBase64 = "AQAB";

        private static string _sessionId;
        private static string _projectRoot;
        private static string _author;
        private static string _projectId;
        private static int _sendIntervalSeconds;
        private static bool _serverEnabled = true;

        private static DateTime _currentDay;
        private static DateTime _firstActivity;
        private static DateTime _lastActivity;
        private static DateTime _lastAccountingTime;
        private static DateTime _lastSendTime;
        private static DateTime _lastConfigFetchTime;
        private static double _activeSeconds;
        private static double _idleSeconds;
        private static double _overtimeActiveSeconds;
        private static bool _hasActivity;
        private static bool _dirty;
        private static bool _initialized;
        private static bool _sending;
        private static bool _fetchingConfig;

        [InitializeOnLoadMethod]
        private static void Initialize()
        {
            if (_initialized)
            {
                return;
            }

            _initialized = true;
            _sessionId = Guid.NewGuid().ToString("N");
            _projectRoot = Directory.GetParent(Application.dataPath).FullName;
            _author = ResolveAuthor();
            _projectId = ResolveProjectId();
            _sendIntervalSeconds = EditorPrefs.GetInt(UALSettings.IntervalKey, DefaultSendIntervalSeconds);
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

            if ((now - _lastConfigFetchTime).TotalSeconds >= 60)
            {
                FetchConfig();
            }

            AccumulateIdleTo(now);

            if (now.Date != _currentDay)
            {
                Flush();
                ResetDay(now.Date);
            }

            if (_hasActivity && _dirty && (now - _lastSendTime).TotalSeconds >= _sendIntervalSeconds)
            {
                SendSnapshot(now);
            }
        }

        private static void RecordActivity()
        {
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

            DateTime now = DateTime.Now;
            AccumulateIdleTo(now);
            SendSnapshot(now);
        }

        private static void ResetDay(DateTime day)
        {
            _currentDay = day;
            _firstActivity = DateTime.MinValue;
            _lastActivity = DateTime.MinValue;
            _lastAccountingTime = DateTime.Now;
            _lastSendTime = DateTime.MinValue;
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
            if (end <= start || !_hasActivity)
            {
                return;
            }

            DateTime workWindowEnd = _firstActivity.AddSeconds(WorkWindowSeconds);

            if (start < workWindowEnd)
            {
                DateTime normalEnd = end > workWindowEnd ? workWindowEnd : end;

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
                DateTime overtimeStart = start < workWindowEnd ? workWindowEnd : start;

                if (end > overtimeStart)
                {
                    _overtimeActiveSeconds += (end - overtimeStart).TotalSeconds;
                }
            }
        }

        private static void FetchConfig()
        {
            if (_fetchingConfig)
            {
                return;
            }

            string serverUrl = UALSettings.ServerUrl.TrimEnd('/');
            string url = serverUrl + "/api/v1/plugins/config?source=" + UnityWebRequest.EscapeURL(Source)
                + "&author=" + UnityWebRequest.EscapeURL(_author)
                + "&projectId=" + UnityWebRequest.EscapeURL(_projectId);
            UnityWebRequest request = UnityWebRequest.Get(url);
            _fetchingConfig = true;
            _lastConfigFetchTime = DateTime.Now;

            UnityWebRequestAsyncOperation operation = request.SendWebRequest();
            operation.completed += _ =>
            {
                _fetchingConfig = false;

                try
                {
                    if (request.result == UnityWebRequest.Result.Success)
                    {
                        PluginConfig config = JsonUtility.FromJson<PluginConfig>(request.downloadHandler.text);
                        _serverEnabled = config.enabled;

                        if (config.sendIntervalSeconds >= 30)
                        {
                            _sendIntervalSeconds = config.sendIntervalSeconds;
                            EditorPrefs.SetInt(UALSettings.IntervalKey, _sendIntervalSeconds);
                        }
                    }
                }
                finally
                {
                    request.Dispose();
                }
            };
        }

        private static void SendSnapshot(DateTime recordedAt)
        {
            if (!_dirty || !_hasActivity || _sending || !_serverEnabled)
            {
                return;
            }

            try
            {
                ActivitySnapshot snapshot = new ActivitySnapshot();
                snapshot.source = Source;
                snapshot.pluginName = PluginName;
                snapshot.pluginVersion = PluginVersion;
                snapshot.author = _author;
                snapshot.projectId = _projectId;
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

                string encryptedPacket = EncryptSnapshot(JsonUtility.ToJson(snapshot));
                SubmitReportRequest body = new SubmitReportRequest();
                body.source = Source;
                body.pluginVersion = PluginVersion;
                body.encryptedPacket = encryptedPacket;

                byte[] bodyBytes = Encoding.UTF8.GetBytes(JsonUtility.ToJson(body));
                string url = UALSettings.ServerUrl.TrimEnd('/') + "/api/v1/reports";
                UnityWebRequest request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST);
                request.uploadHandler = new UploadHandlerRaw(bodyBytes);
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                _sending = true;

                UnityWebRequestAsyncOperation operation = request.SendWebRequest();
                operation.completed += _ =>
                {
                    _sending = false;

                    try
                    {
                        if (request.result == UnityWebRequest.Result.Success)
                        {
                            _lastSendTime = recordedAt;
                            _dirty = false;
                        }
                        else
                        {
                            UnityEngine.Debug.LogWarning("UAL failed to submit activity report: " + request.error);
                        }
                    }
                    finally
                    {
                        request.Dispose();
                    }
                };
            }
            catch (Exception exception)
            {
                _sending = false;
                UnityEngine.Debug.LogWarning("UAL failed to build activity report: " + exception.Message);
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
                WriteAscii(stream, "ALR1");
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
            string authorOverride = UALSettings.AuthorOverride.Trim();

            if (!string.IsNullOrEmpty(authorOverride))
            {
                return authorOverride;
            }

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

        private static string ResolveProjectId()
        {
            string projectId = UALSettings.ProjectId.Trim();

            if (!string.IsNullOrEmpty(projectId))
            {
                return projectId;
            }

            return Regex.Replace(Application.productName.ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-');
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

        [Serializable]
        private class PluginConfig
        {
            public string source;
            public string author;
            public string projectId;
            public bool enabled;
            public int sendIntervalSeconds;
        }

        [Serializable]
        private class SubmitReportRequest
        {
            public string source;
            public string pluginVersion;
            public string encryptedPacket;
        }

        [Serializable]
        private class ActivitySnapshot
        {
            public string source;
            public string pluginName;
            public string pluginVersion;
            public string author;
            public string projectId;
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

    internal static class UALSettings
    {
        public const string ServerUrlKey = "AL.UAL.ServerUrl";
        public const string AuthorOverrideKey = "AL.UAL.AuthorOverride";
        public const string ProjectIdKey = "AL.UAL.ProjectId";
        public const string IntervalKey = "AL.UAL.IntervalSeconds";

        public static string ServerUrl
        {
            get { return EditorPrefs.GetString(ServerUrlKey, "http://127.0.0.1:8000"); }
            set { EditorPrefs.SetString(ServerUrlKey, value); }
        }

        public static string AuthorOverride
        {
            get { return EditorPrefs.GetString(AuthorOverrideKey, string.Empty); }
            set { EditorPrefs.SetString(AuthorOverrideKey, value); }
        }

        public static string ProjectId
        {
            get { return EditorPrefs.GetString(ProjectIdKey, string.Empty); }
            set { EditorPrefs.SetString(ProjectIdKey, value); }
        }
    }

    internal static class UALSettingsProvider
    {
        [SettingsProvider]
        public static SettingsProvider CreateSettingsProvider()
        {
            SettingsProvider provider = new SettingsProvider("Project/UAL", SettingsScope.Project)
            {
                label = "UAL",
                guiHandler = _ =>
                {
                    EditorGUILayout.LabelField("Server", EditorStyles.boldLabel);
                    UALSettings.ServerUrl = EditorGUILayout.TextField("Server URL", UALSettings.ServerUrl);

                    EditorGUILayout.Space();
                    EditorGUILayout.LabelField("Identity", EditorStyles.boldLabel);
                    UALSettings.AuthorOverride = EditorGUILayout.TextField("Author Override", UALSettings.AuthorOverride);
                    UALSettings.ProjectId = EditorGUILayout.TextField("Project ID", UALSettings.ProjectId);

                    EditorGUILayout.Space();
                    EditorGUILayout.HelpBox("The backend can override the send interval per author. The local value is used until backend config is fetched.", MessageType.Info);
                    int interval = EditorPrefs.GetInt(UALSettings.IntervalKey, UAL.DefaultSendIntervalSeconds);
                    interval = EditorGUILayout.IntField("Fallback Interval Seconds", interval);
                    EditorPrefs.SetInt(UALSettings.IntervalKey, Math.Max(30, interval));
                },
                keywords = new HashSet<string>(new[] { "activity", "logger", "ual", "server" })
            };

            return provider;
        }
    }
}
#endif
