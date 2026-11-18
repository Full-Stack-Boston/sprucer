import Link from "next/link";
import { DeskVisual } from "@/components/DeskVisual";

export default function HomePage() {
  return (
    <section className="hero-split">
      <div className="hero-copy">
        <div className="eyebrow">Truth-first career desk</div>
        <h1>Write applications you can defend.</h1>
        <p>
          Keep a knowledge bank of facts you will stand behind. Ingest a job description.
          Draft cover letters and notes through any OpenAI-compatible model, grounded only
          in that truth. Nothing leaves as final until you approve it.
        </p>
        <div className="actions">
          <Link className="btn" href="/applications">
            Open applications
          </Link>
          <Link className="btn secondary" href="/knowledge">
            Edit knowledge bank
          </Link>
        </div>
        <ul className="hero-points">
          <li>Grounded in your vault</li>
          <li>Drafts until you approve</li>
          <li>Any OpenAI-compatible model</li>
        </ul>
      </div>
      <div className="hero-art" aria-hidden="false">
        <DeskVisual />
      </div>
    </section>
  );
}
