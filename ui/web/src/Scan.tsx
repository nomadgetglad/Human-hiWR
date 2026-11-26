// Scan (HiWR button): take a photo and read a QR code, a UPC/EAN barcode, or the text on it.
// Everything runs in this browser: the photo and the text never leave the device. Codes are then looked
// up in the business's own records (products, tickets, dishes), filtered by level on the server.
import { useEffect, useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import { BrowserMultiFormatReader } from "@zxing/browser";
import { BarcodeFormat, DecodeHintType } from "@zxing/library";
import { api } from "./api";
import { useT } from "./i18n";

type Found = { kind: "QR code" | "UPC label" | "Barcode" | "Text"; text: string; format?: string };
type Match = { type: string; id: string; title: string; detail: string };

const UPC = new Set([BarcodeFormat.UPC_A, BarcodeFormat.UPC_E, BarcodeFormat.EAN_13, BarcodeFormat.EAN_8]);

function reader() {
  const hints = new Map();
  hints.set(DecodeHintType.POSSIBLE_FORMATS, [
    BarcodeFormat.QR_CODE, BarcodeFormat.UPC_A, BarcodeFormat.UPC_E, BarcodeFormat.EAN_13, BarcodeFormat.EAN_8,
    BarcodeFormat.CODE_128, BarcodeFormat.CODE_39,
  ]);
  hints.set(DecodeHintType.TRY_HARDER, true);
  return new BrowserMultiFormatReader(hints);
}

async function readCode(url: string): Promise<Found | null> {
  try {
    const r = await reader().decodeFromImageUrl(url);
    const f = r.getBarcodeFormat();
    return { kind: f === BarcodeFormat.QR_CODE ? "QR code" : UPC.has(f) ? "UPC label" : "Barcode", text: r.getText(), format: BarcodeFormat[f] };
  } catch {
    return null; // no code in the photo
  }
}

export async function readText(url: string): Promise<Found | null> {
  // Loaded only when needed; the reader and English data are served from this app (public/ocr), not the internet.
  const { createWorker } = await import("tesseract.js");
  const base = `${location.origin}/ocr`;
  const worker = await createWorker("eng", 1, { workerPath: `${base}/worker.min.js`, corePath: base, langPath: base, workerBlobURL: false });
  try {
    const { data } = await worker.recognize(url);
    const text = data.text.replace(/\n{3,}/g, "\n\n").trim();
    return text ? { kind: "Text", text } : null;
  } finally {
    await worker.terminate();
  }
}

export function ScanDialog({ isOpen, onClose, sample }: { isOpen: boolean; onClose: () => void; sample?: string | null }) {
  const t = useT();
  const [photo, setPhoto] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [found, setFound] = useState<Found | null>(null);
  const [matches, setMatches] = useState<Match[] | null>(null);
  const [live, setLive] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const stopCamera = () => {
    stream.current?.getTracks().forEach((tr) => tr.stop());
    stream.current = null;
    setLive(false);
  };
  const reset = () => {
    stopCamera();
    setPhoto(null);
    setFound(null);
    setMatches(null);
    setStatus("");
  };
  useEffect(() => {
    if (!isOpen) reset();
    else if (sample) analyze(sample);
  }, [isOpen, sample]);

  const analyze = async (url: string, textToo = false) => {
    setPhoto(url);
    setFound(null);
    setMatches(null);
    setStatus(t("Looking for a QR code or barcode…"));
    let f = textToo ? null : await readCode(url);
    if (!f) {
      setStatus(t("No code found. Reading the text… (the first time takes a few seconds)"));
      try {
        f = await readText(url);
      } catch (e: any) {
        setStatus(`${t("Couldn't read text")}: ${e.message ?? e}`);
        return;
      }
    }
    setFound(f);
    setStatus(f ? "" : t("Nothing readable in this photo. Try closer, flatter and with more light."));
    if (f && f.kind !== "Text") {
      try {
        setMatches((await api<{ matches: Match[] }>("/api/scan/lookup", { code: f.text, kind: f.kind })).matches);
      } catch {
        setMatches([]);
      }
    }
  };

  const startCamera = async () => {
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      setLive(true);
      requestAnimationFrame(() => {
        if (video.current) {
          video.current.srcObject = stream.current;
          video.current.play();
        }
      });
    } catch {
      setStatus(t("No camera here, or it was not allowed. Use Take or choose a photo instead."));
    }
  };
  const snap = () => {
    const v = video.current;
    if (!v) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    stopCamera();
    analyze(c.toDataURL("image/jpeg", 0.92));
  };

  return (
    <ModalOverlay className="overlay" isOpen={isOpen} onOpenChange={(o) => !o && onClose()} isDismissable>
      <Modal className="card scan-card">
        <Dialog className="scan" aria-label="Scan">
          <Heading slot="title" className="h2">
            📷 {t("Scan")}
          </Heading>
          <p className="muted small">{t("QR code, UPC label or printed text. The photo stays on this device.")}</p>
          <input
            ref={file}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) analyze(URL.createObjectURL(f));
              e.target.value = "";
            }}
          />
          {!live && (
            <div className="row-gap">
              <Button className="btn btn-primary" onPress={() => file.current?.click()}>
                {t("Take or choose a photo")}
              </Button>
              <Button className="btn" onPress={startCamera}>
                {t("Use this device's camera")}
              </Button>
            </div>
          )}
          {live && (
            <div className="scan-live">
              <video ref={video} playsInline muted />
              <div className="row-gap">
                <Button className="btn btn-primary" onPress={snap}>
                  {t("Take photo")}
                </Button>
                <Button className="btn" onPress={stopCamera}>
                  {t("Cancel")}
                </Button>
              </div>
            </div>
          )}
          {photo && <img className="scan-photo" src={photo} alt={t("Your photo")} />}
          {status && <p className="small" role="status">{status}</p>}
          {found && (
            <div className="panel scan-result">
              <span className="badge">{t(found.kind)}</span> {found.format && <span className="muted small">{found.format}</span>}
              <pre className="scan-text">{found.text}</pre>
              <div className="row-gap">
                <Button className="btn btn-sm" onPress={() => navigator.clipboard?.writeText(found.text)}>
                  {t("Copy")}
                </Button>
                {found.kind !== "Text" && photo && (
                  <Button className="btn btn-sm" onPress={() => analyze(photo, true)}>
                    {t("Read the text instead")}
                  </Button>
                )}
              </div>
              {matches && (
                <>
                  <h3 className="h3">{t("In your records")}</h3>
                  {matches.length === 0 ? (
                    <p className="muted small">{t("Not found in your products, tickets or dishes.")}</p>
                  ) : (
                    <ul className="settings-list">
                      {matches.map((m) => (
                        <li key={`${m.type}-${m.id}`}>
                          <span className="badge">{t(m.type)}</span> <b>{m.title}</b> <span className="muted small">· {m.detail}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          )}
          <div className="row-gap scan-foot">
            {photo && (
              <Button className="btn" onPress={reset}>
                {t("Scan another")}
              </Button>
            )}
            <Button className="btn btn-ghost" slot="close" onPress={onClose}>
              {t("Close")}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
