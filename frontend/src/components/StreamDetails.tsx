import type { StreamInfo } from "../types";

function value(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "Unavailable" : String(value);
}

export default function StreamDetails({ streams }: { streams: StreamInfo[] }) {
  if (!streams.length) return <p className="detail-empty">Stream details unavailable.</p>;
  return (
    <div className="stream-list">
      {streams.map((stream, index) => (
        <div className="stream-row" key={`${stream.stream_type}-${stream.codec}-${index}`}>
          <span className="stream-type">{stream.stream_type}</span>
          <span>{value(stream.codec)}</span>
          <span>{value(stream.language)}</span>
          {stream.stream_type === "video" && (
            <span>{stream.width && stream.height ? `${stream.width} × ${stream.height}` : "Dimensions unavailable"}</span>
          )}
          {stream.stream_type === "audio" && (
            <span>{stream.channels ? `${stream.channels} ch${stream.channel_layout ? ` · ${stream.channel_layout}` : ""}` : "Channels unavailable"}</span>
          )}
          <span>{value(stream.profile)}</span>
          <span>{value(stream.frame_rate)}</span>
          {stream.dynamic_range && <span>{stream.dynamic_range}</span>}
        </div>
      ))}
    </div>
  );
}
