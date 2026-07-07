/* VITAL mobile — Expo React Native client for the same FastAPI backend.
 *
 * Identity: X-Vital-Session header (server-issued, stored in SecureStore)
 * instead of httponly cookies — RN networking doesn't handle those.
 * Streaming: expo/fetch (SDK 52+) supports response.body streaming, which
 * plain RN fetch does not. SSE parsing mirrors vital-web.
 */
import Constants from "expo-constants";
import * as DocumentPicker from "expo-document-picker";
import * as SecureStore from "expo-secure-store";
import { fetch as streamFetch } from "expo/fetch";
import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import {
  FlatList, KeyboardAvoidingView, Modal, Platform, Pressable,
  StyleSheet, Text, TextInput, View,
} from "react-native";

// Env override first (never commit a personal LAN IP into app.json):
//   EXPO_PUBLIC_API_BASE=http://192.168.x.x:8000 pnpm start
const API = process.env.EXPO_PUBLIC_API_BASE
  ?? Constants?.expoConfig?.extra?.apiBase
  ?? "http://10.0.2.2:8000";
const SESSION_KEY = "vital_session";

const STARTERS = [
  "I slept 4 hours and I'm somehow buzzing with energy",
  "Bored — what should I do this weekend?",
  "Find me people who are into bouldering",
];

async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split(/\r?\n\r?\n/);
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      let event = "message", data = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        // SSE spec: strip exactly ONE leading space — token chunks may
        // legitimately start with whitespace ("Iunderstand" bug)
        else if (line.startsWith("data:")) {
          const v = line.slice(5);
          data.push(v.startsWith(" ") ? v.slice(1) : v);
        }
      }
      yield { event, data: data.join("\n") };
    }
  }
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [pendingPlan, setPendingPlan] = useState(null);
  const [editText, setEditText] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [memories, setMemories] = useState(null);
  // Block requests until the stored session is loaded — otherwise the first
  // message races the read and creates a SECOND identity for the same user.
  const [sessionReady, setSessionReady] = useState(false);
  const session = useRef(null);
  const listRef = useRef(null);
  const msgSeq = useRef(0);  // stable ids for streaming bubbles (no crypto.randomUUID in Hermes)

  useEffect(() => {
    SecureStore.getItemAsync(SESSION_KEY)
      .then((s) => { session.current = s; })
      .catch(() => {})           // no stored session is fine — server issues one
      .finally(() => setSessionReady(true));
  }, []);

  function headers(extra = {}) {
    const h = { ...extra };
    if (session.current) h["X-Vital-Session"] = session.current;
    return h;
  }

  async function captureSession(response) {
    const s = response.headers.get("x-vital-session");
    if (s) {
      session.current = s;
      await SecureStore.setItemAsync(SESSION_KEY, s).catch(() => {});
    }
  }

  async function consume(response) {
    await captureSession(response);
    // Stable-id updates (mirrors vital-web fix): a late empty event must
    // never clobber streamed text or delete the wrong bubble.
    const id = `ai-${++msgSeq.current}`;
    let aiText = "";
    setMessages((m) => [...m, { id, role: "ai", text: "" }]);
    const sync = (status = null) => setMessages((m) => m.map((msg) =>
      msg.id === id ? { ...msg, text: aiText, status } : msg));

    for await (const { event, data } of sseEvents(response)) {
      if (event === "token") {
        aiText += data;
        sync();
      } else if (event === "message") {
        if (data) { aiText = data; sync(); }  // ignore empty terminal events
      } else if (event === "status") {
        sync(data);
      } else if (event === "approval_required") {
        setPendingPlan(JSON.parse(data).plan);
      }
    }
    sync(); // clear lingering status line
    if (!aiText) {
      // no text streamed — remove the placeholder even on approval flows,
      // otherwise an empty bubble sits above the plan card
      setMessages((m) => m.filter((msg) => msg.id !== id));
    }
  }

  async function apiError(r) {
    // best-effort detail extraction; never throw while reporting an error
    try {
      const body = await r.json();
      return body.detail || `Server error (${r.status})`;
    } catch {
      return `Server error (${r.status})`;
    }
  }

  async function send(text) {
    if (!text.trim() || busy || !sessionReady) return;
    setBusy(true);
    setInput("");
    setPendingPlan(null);
    setMessages((m) => [...m, { role: "user", text }]);
    try {
      const r = await streamFetch(`${API}/chat`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ message: text, thread_id: "mobile" }),
      });
      if (!r.ok) {
        const errText = await apiError(r);
        setMessages((m) => [...m, { role: "ai", text: errText }]);
      } else {
        await consume(r);
      }
    } catch {
      setMessages((m) => [...m, { role: "ai", text: "Can't reach the backend." }]);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action, feedback = "") {
    if (busy || !sessionReady) return;
    setBusy(true);
    setPendingPlan(null);
    setEditText("");
    try {
      const r = await streamFetch(`${API}/approve`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ action, feedback, thread_id: "mobile" }),
      });
      if (!r.ok) {
        const errText = await apiError(r);
        setMessages((m) => [...m, { role: "ai", text: errText }]);
      } else {
        await consume(r);
      }
    } catch {
      setMessages((m) => [...m, { role: "ai", text: "Can't reach the backend." }]);
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (busy || !sessionReady) return;
    try {
      const picked = await DocumentPicker.getDocumentAsync({
        type: ["text/csv", "text/xml", "application/xml", "text/comma-separated-values"],
      });
      if (picked.canceled) return;
      const asset = picked.assets[0];
      const form = new FormData();
      form.append("file", { uri: asset.uri, name: asset.name, type: asset.mimeType });
      const r = await fetch(`${API}/upload/health`, {
        method: "POST", headers: headers(), body: form,
      });
      await captureSession(r);
      if (!r.ok) {
        const errText = await apiError(r);
        setMessages((m) => [...m, { role: "ai", text: `Upload failed: ${errText}` }]);
        return;
      }
      const body = await r.json();
      setMessages((m) => [...m, {
        role: "ai",
        text: `Imported ${body.nights_imported} nights (${body.date_range[0]} → ${body.date_range[1]}). Ask me about your sleep.`,
      }]);
    } catch {
      setMessages((m) => [...m, { role: "ai", text: "Upload failed — can't reach the backend." }]);
    }
  }

  async function toggleMemories() {
    if (memories) return setMemories(null);
    if (!sessionReady) return;
    try {
      const r = await fetch(`${API}/memories`, { headers: headers() });
      await captureSession(r);
      if (!r.ok) return;
      setMemories((await r.json()).memories);
    } catch {
      // drawer just doesn't open; chat still shows connectivity errors
    }
  }

  async function forget(key) {
    try {
      const r = await fetch(`${API}/memories/${key}`, { method: "DELETE", headers: headers() });
      if (r.ok) setMemories((mems) => mems.filter((m) => m.key !== key));
    } catch {
      // keep the row; user can retry
    }
  }

  const renderMessage = ({ item }) => {
    if (item.role === "ai" && !item.text && !item.status) return null; // defensive: never show empty bubbles
    return (
      <View style={[s.msg, item.role === "user" ? s.msgUser : s.msgAi]}>
        <Text style={item.role === "user" ? s.msgUserText : s.msgAiText}>{item.text}</Text>
        {item.status ? <Text style={s.status}>{item.status}…</Text> : null}
      </View>
    );
  };

  return (
    <KeyboardAvoidingView style={s.root}
      behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <StatusBar style="light" />
      <View style={s.top}>
        <Text style={s.logo}>VITAL<Text style={{ color: "#5eead4" }}>.</Text></Text>
        <View style={s.topActions}>
          <Pressable style={s.btn} onPress={upload}><Text style={s.btnText}>Upload sleep</Text></Pressable>
          <Pressable style={s.btn} onPress={toggleMemories}><Text style={s.btnText}>Knows me</Text></Pressable>
        </View>
      </View>

      <FlatList
        ref={listRef}
        data={messages}
        renderItem={renderMessage}
        keyExtractor={(item, i) => item.id ?? String(i)}
        contentContainerStyle={{ padding: 16, paddingBottom: 24 }}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={
          <View>
            <Text style={s.hint}>Sleep, energy, activities, ideas, people — agents that know you. Try:</Text>
            {STARTERS.map((t) => (
              <Pressable key={t} style={s.starter} onPress={() => send(t)}>
                <Text style={s.btnText}>{t}</Text>
              </Pressable>
            ))}
          </View>
        }
        ListFooterComponent={pendingPlan && (
          <View style={s.planCard}>
            <Text style={s.planTitle}>Proposed plan — your call</Text>
            {pendingPlan.items.map((it, i) => (
              <Text key={i} style={s.planItem}>
                {it.day} {it.start}–{it.end}  ·  {it.title}  ({it.kind})
              </Text>
            ))}
            {pendingPlan.tradeoffs && pendingPlan.tradeoffs !== "none" ? (
              <Text style={s.tradeoffs}>Tradeoffs: {pendingPlan.tradeoffs}</Text>
            ) : null}
            <View style={s.planActions}>
              <Pressable style={[s.btn, s.btnPrimary]} onPress={() => decide("approve")}>
                <Text style={s.btnPrimaryText}>Approve</Text>
              </Pressable>
              <Pressable style={s.btn} onPress={() => decide("reject")}>
                <Text style={[s.btnText, { color: "#f87171" }]}>Reject</Text>
              </Pressable>
            </View>
            <TextInput style={s.input} placeholder="…or ask for a change"
              placeholderTextColor="#9aa3b5" value={editText}
              onChangeText={setEditText}
              onSubmitEditing={() => editText && decide("edit", editText)} />
          </View>
        )}
      />

      <Modal visible={!!memories} transparent animationType="slide"
        onRequestClose={() => setMemories(null)}>
        <View style={s.modalWrap}>
          <View style={s.modal}>
            <Text style={s.planTitle}>What VITAL knows about you</Text>
            {memories?.length === 0 && <Text style={s.hint}>Nothing yet — it learns as you chat.</Text>}
            {memories?.map((m) => (
              <View key={m.key} style={s.memRow}>
                <Text style={[s.msgAiText, { flex: 1 }]}>{m.fact}</Text>
                <Pressable onPress={() => forget(m.key)}>
                  <Text style={{ color: "#f87171", fontSize: 12 }}>forget</Text>
                </Pressable>
              </View>
            ))}
            <Pressable style={[s.btn, { marginTop: 12 }]} onPress={() => setMemories(null)}>
              <Text style={s.btnText}>Close</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      <View style={s.composer}>
        <TextInput style={[s.input, { flex: 1 }]} value={input}
          placeholder={!sessionReady ? "loading…" : busy ? "thinking…" : "Talk to VITAL"}
          placeholderTextColor="#9aa3b5" editable={!busy && sessionReady}
          onChangeText={setInput} onSubmitEditing={() => send(input)} />
        <Pressable style={[s.btn, s.btnPrimary]} disabled={busy || !sessionReady}
          onPress={() => send(input)}>
          <Text style={s.btnPrimaryText}>Send</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#0f1115" },
  top: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingHorizontal: 16, paddingTop: 56, paddingBottom: 12,
    borderBottomWidth: 1, borderBottomColor: "#262b38",
  },
  logo: { color: "#e8eaf0", fontSize: 20, fontWeight: "700", letterSpacing: 1 },
  topActions: { flexDirection: "row", gap: 8 },
  btn: {
    backgroundColor: "#1e2230", borderColor: "#2c3346", borderWidth: 1,
    borderRadius: 10, paddingVertical: 7, paddingHorizontal: 12,
  },
  btnText: { color: "#e8eaf0", fontSize: 13 },
  btnPrimary: { backgroundColor: "#5eead4", borderColor: "#5eead4" },
  btnPrimaryText: { color: "#08221d", fontWeight: "600", fontSize: 13 },
  hint: { color: "#9aa3b5", fontSize: 13, marginBottom: 12 },
  starter: {
    backgroundColor: "#1e2230", borderRadius: 10, padding: 12, marginBottom: 8,
    borderWidth: 1, borderColor: "#2c3346",
  },
  msg: { borderRadius: 14, padding: 12, marginBottom: 10, maxWidth: "85%" },
  msgUser: { backgroundColor: "#818cf8", alignSelf: "flex-end" },
  msgAi: { backgroundColor: "#171a21", alignSelf: "flex-start" },
  msgUserText: { color: "#0d0f1e", fontSize: 15 },
  msgAiText: { color: "#e8eaf0", fontSize: 15 },
  status: { color: "#9aa3b5", fontSize: 11, fontStyle: "italic", marginTop: 4 },
  planCard: {
    backgroundColor: "#171a21", borderColor: "#5eead4", borderWidth: 1,
    borderRadius: 14, padding: 14, marginTop: 8,
  },
  planTitle: { color: "#5eead4", fontSize: 14, fontWeight: "600", marginBottom: 8 },
  planItem: { color: "#e8eaf0", fontSize: 13, paddingVertical: 3 },
  tradeoffs: { color: "#9aa3b5", fontSize: 12, fontStyle: "italic", marginTop: 6 },
  planActions: { flexDirection: "row", gap: 8, marginTop: 10, marginBottom: 8 },
  input: {
    backgroundColor: "#171a21", color: "#e8eaf0", borderColor: "#2c3346",
    borderWidth: 1, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 15,
  },
  composer: {
    flexDirection: "row", gap: 8, padding: 12, paddingBottom: 28,
    borderTopWidth: 1, borderTopColor: "#262b38", alignItems: "center",
  },
  modalWrap: { flex: 1, justifyContent: "flex-end", backgroundColor: "#000a" },
  modal: {
    backgroundColor: "#171a21", borderTopLeftRadius: 18, borderTopRightRadius: 18,
    padding: 20, paddingBottom: 40,
  },
  memRow: {
    flexDirection: "row", gap: 10, paddingVertical: 8,
    borderTopWidth: 1, borderTopColor: "#232838", alignItems: "center",
  },
});
