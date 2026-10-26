import { FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { api, ApiError } from "./api";
import { movies as demoMovies } from "./data";
import type { GroupRecommendationMode, GroupRoom, GroupSession, Movie, MovieAction, PreferenceResult, Profile, SwipeAction, User } from "./types";

const colors = ["#f6b84b", "#77cbb9", "#dd7d91", "#9b8ee8"];

const ONBOARDING_GENRES = [
  "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama",
  "Fantasy", "Horror", "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western",
] as const;

const GROUP_RECOMMENDATION_MODES: {
  id: GroupRecommendationMode;
  title: string;
  description: string;
  formula: string;
  recommended?: boolean;
}[] = [
  {
    id: "average",
    title: "Crowd Favorite",
    description: "Prioritizes the highest average enjoyment. Best when the room already has similar tastes.",
    formula: "Average member score",
  },
  {
    id: "least_misery",
    title: "No Regrets",
    description: "Protects the least-satisfied person so nobody gets stuck with a movie they strongly dislike.",
    formula: "Highest minimum score",
  },
  {
    id: "fair_consensus",
    title: "Balanced Mix",
    description: "Balances overall enjoyment with the least-satisfied member and penalizes sharp disagreement.",
    formula: "Average + minimum − disagreement",
    recommended: true,
  },
  {
    id: "nash",
    title: "All On Board",
    description: "Rewards choices that every member scores reasonably well, instead of one person's perfect pick.",
    formula: "Geometric mean",
  },
];

function groupModeTitle(mode: GroupRecommendationMode): string {
  return GROUP_RECOMMENDATION_MODES.find((option) => option.id === mode)?.title ?? mode;
}

function RecommendationModePicker({
  roomId,
  value,
  onChange,
  actionLabel,
  onAction,
  actionDisabled,
  actionPending,
}: {
  roomId: number;
  value: GroupRecommendationMode;
  onChange: (mode: GroupRecommendationMode) => void;
  actionLabel: string;
  onAction: () => void;
  actionDisabled?: boolean;
  actionPending?: boolean;
}) {
  return (
    <fieldset className="recommendation-mode-picker">
      <legend>How should CineMatch choose for this room?</legend>
      <p>The personal ML model scores movies for each member first. This mode controls how those scores are combined.</p>
      <div className="recommendation-mode-grid">
        {GROUP_RECOMMENDATION_MODES.map((mode) => (
          <label className="recommendation-mode" key={mode.id}>
            <input
              type="radio"
              name={`recommendation-mode-${roomId}`}
              value={mode.id}
              checked={value === mode.id}
              onChange={() => onChange(mode.id)}
            />
            <span>
              <strong>{mode.title}{mode.recommended ? " · Recommended" : ""}</strong>
              <small>{mode.description}</small>
              <em>{mode.formula}</em>
            </span>
          </label>
        ))}
      </div>
      <button className="button primary" type="button" onClick={onAction} disabled={actionDisabled || actionPending}>
        {actionPending ? "Saving…" : actionLabel}
      </button>
    </fieldset>
  );
}

function profileNeedsQuestions(profile: Profile | undefined): boolean {
  return !!profile && profile.id > 0 && !(profile.seedPreferredGenres?.length);
}

function useRemote<T>(loader: () => Promise<T>, dependencies: unknown[] = []) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let current = true;
    setLoading(true);
    loader().then((result) => current && setData(result)).catch((reason: Error) => current && setError(reason.message)).finally(() => current && setLoading(false));
    return () => { current = false; };
    // The caller explicitly controls refresh dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
  return { data, error, loading };
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profilesReady, setProfilesReady] = useState(false);
  const [activeId, setActiveId] = useState(() => Number(localStorage.getItem("cinematch:activeProfile") ?? 0));
  const [readOnly, setReadOnly] = useState(false);
  const [notice, setNotice] = useState("");
  const [feedRevision, setFeedRevision] = useState(0);
  const activeProfile = profiles.find((profile) => profile.id === activeId) ?? profiles[0];
  const bumpFeed = () => setFeedRevision((value) => value + 1);
  useEffect(() => {
    api.restoreSession()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setAuthChecked(true));
  }, []);
  useEffect(() => {
    if (!user) {
      setProfiles([]);
      setProfilesReady(true);
      return;
    }
    let current = true;
    setProfilesReady(false);
    const syncProfiles = async () => {
      const local = (() => {
        try { return JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]") as Profile[]; } catch { return []; }
      })();
      const next = await api.syncProfiles(local);
      if (!current) return;
      setProfiles(next);
      api.saveProfiles(next);
      const preferred = next.find((profile) => profile.id === activeId) ?? next[0];
      if (preferred) {
        setActiveId(preferred.id);
        localStorage.setItem("cinematch:activeProfile", String(preferred.id));
      }
      setProfilesReady(true);
    };
    void syncProfiles();
    return () => { current = false; };
  }, [user?.id]);
  useEffect(() => {
    api.settings().then((value) => setReadOnly(value.readOnly)).catch(() => undefined);
  }, []);

  const persistProfiles = (next: Profile[]) => {
    setProfiles(next);
    api.saveProfiles(next);
  };
  const selectProfile = (id: number) => {
    setActiveId(id);
    localStorage.setItem("cinematch:activeProfile", String(id));
  };
  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 2800);
  };
  const location = useLocation();
  const logout = () => { void api.logout().finally(() => setUser(null)); };
  const app = { profiles, activeProfile, persistProfiles, selectProfile, readOnly, setReadOnly, flash, feedRevision, bumpFeed };

  if (!authChecked) return <div className="loading-page">Restoring your CineMatch session…</div>;
  if (!user) return <AuthPage onAuthenticated={setUser} />;
  if (!profilesReady) return <div className="loading-page">Loading your profiles…</div>;

  const questionGate =
    profileNeedsQuestions(activeProfile) &&
    !location.pathname.startsWith("/onboarding/questions") &&
    location.pathname !== "/settings";

  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">Skip to content</a>
      <Header
        profiles={profiles}
        activeProfile={activeProfile}
        selectProfile={selectProfile}
        user={user}
        logout={logout}
      />
      {readOnly && <div className="readonly-banner" role="status">Read-only mode — browsing works, but changes are disabled.</div>}
      {notice && <div className="toast" role="status">{notice}</div>}
      <main id="content">
        {questionGate ? (
          <Navigate to="/onboarding/questions" replace />
        ) : (
        <Routes>
          <Route path="/" element={profiles.length ? <Home {...app} /> : <Profiles {...app} />} />
          <Route path="/profiles" element={<Profiles {...app} />} />
          <Route path="/onboarding/questions" element={<OnboardingQuestions {...app} />} />
          <Route path="/onboarding" element={<Onboarding {...app} />} />
          <Route path="/search" element={<Search {...app} />} />
          <Route path="/movies/:id" element={<MovieDetail {...app} />} />
          <Route path="/group" element={<Group {...app} />} />
          <Route path="/invite/:token" element={<JoinInvite {...app} />} />
          <Route path="/wishlist" element={<WishlistView {...app} />} />
          <Route path="/evaluation" element={<Navigate to="/wishlist" replace />} />
          <Route path="/settings" element={<SettingsView setReadOnly={setReadOnly} user={user} setUser={setUser} logout={logout} bumpFeed={bumpFeed} />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
        )}
      </main>
      <Footer />
    </div>
  );
}

type SharedProps = {
  profiles: Profile[];
  activeProfile?: Profile;
  persistProfiles: (profiles: Profile[]) => void;
  selectProfile: (id: number) => void;
  readOnly: boolean;
  setReadOnly: (value: boolean) => void;
  flash: (message: string) => void;
  feedRevision: number;
  bumpFeed: () => void;
};

function Header({ profiles, activeProfile, selectProfile, user, logout }: Pick<SharedProps, "profiles" | "activeProfile" | "selectProfile"> & { user: User; logout: () => void }) {
  return (
    <header className="header">
      <Link to="/" className="brand" aria-label="CineMatch home"><span>Cine</span>Match<i /></Link>
      <nav aria-label="Main navigation">
        <NavLink to="/">For you</NavLink><NavLink to="/onboarding">Discover</NavLink><NavLink to="/search">Search</NavLink><NavLink to="/group">Group</NavLink><NavLink to="/wishlist">Want to watch</NavLink>
      </nav>
      <div className="header-actions">
        <span className="signed-in-user">{user.username}</span>
        <Link className="icon-button" to="/settings" aria-label="Settings">⚙</Link>
        <button className="button quiet" onClick={logout}>Log out</button>
        {profiles.length > 0 && (
          <label className="profile-select"><span className="sr-only">Active profile</span>
            <select value={activeProfile?.id ?? ""} onChange={(event) => selectProfile(Number(event.target.value))}>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </label>
        )}
      </div>
    </header>
  );
}

function AuthPage({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [profileName, setProfileName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const authenticated = mode === "login"
        ? await api.login(email, password)
        : await api.register(username, email, password, profileName || username);
      onAuthenticated(authenticated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Authentication failed");
    } finally {
      setPending(false);
    }
  };
  return (
    <main className="auth-page">
      <section className="auth-card card">
        <div className="brand auth-brand"><span>Cine</span>Match<i /></div>
        <p className="eyebrow">Your movie discovery space</p>
        <h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
        <form onSubmit={submit}>
          {mode === "register" && <>
            <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} required /></label>
            <label>Profile name<input value={profileName} onChange={(event) => setProfileName(event.target.value)} required /></label>
          </>}
          <label>Email<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <label>Password<input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={mode === "register" ? 12 : 1} required /></label>
          {mode === "register" && <p className="compromise-note">Password must be at least 12 characters.</p>}
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="button primary" disabled={pending}>{pending ? "Please wait…" : mode === "login" ? "Log in" : "Register"}</button>
        </form>
        <button className="button quiet" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "Need an account? Register" : "Already registered? Log in"}
        </button>
      </section>
    </main>
  );
}

function Profiles({ profiles, activeProfile, persistProfiles, selectProfile, readOnly, flash }: SharedProps) {
  const [name, setName] = useState("");
  const navigate = useNavigate();
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (readOnly || !name.trim()) return;
    let profile: Profile;
    try {
      profile = await api.createProfile(name.trim());
      profile = { ...profile, color: colors[profiles.length % colors.length] };
    } catch {
      profile = { id: -Date.now(), name: name.trim(), color: colors[profiles.length % colors.length], onboarded: false };
    }
    persistProfiles([...profiles, profile]);
    selectProfile(profile.id);
    flash(`${profile.name}'s profile created`);
    navigate("/onboarding/questions");
  };
  const profileDestination = (profile: Profile) => {
    if (profileNeedsQuestions(profile)) return "/onboarding/questions";
    if (!profile.onboarded) return "/onboarding";
    return "/";
  };
  return (
    <Page eyebrow="Your space" title={profiles.length ? "Who's watching?" : "Let’s make this yours"} intro="Separate profiles keep every recommendation personal. Everything stays local until a backend is connected.">
      {profiles.length > 0 && <div className="profile-grid">{profiles.map((profile) => (
        <button className={`profile-card ${profile.id === activeProfile?.id ? "active" : ""}`} key={profile.id} onClick={() => { selectProfile(profile.id); navigate(profileDestination(profile)); }}>
          <span className="avatar" style={{ background: profile.color }}>{profile.name[0].toUpperCase()}</span>
          <strong>{profile.name}</strong><small>{profile.onboarded ? "Ready to watch" : "Finish setup"}</small>
        </button>
      ))}</div>}
      <form className="create-profile card" onSubmit={create}>
        <label htmlFor="profile-name">{profiles.length ? "Add another profile" : "What should we call you?"}</label>
        <div className="inline-form"><input id="profile-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Profile name" maxLength={30} required /><button className="button primary" disabled={readOnly}>Create profile</button></div>
      </form>
    </Page>
  );
}

function OnboardingQuestions({ activeProfile, profiles, persistProfiles, readOnly, flash, bumpFeed }: SharedProps) {
  const [preferred, setPreferred] = useState<string[]>([]);
  const [avoid, setAvoid] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const toggle = (genre: string, list: string[], setList: (value: string[]) => void, other: string[], setOther: (value: string[]) => void) => {
    if (list.includes(genre)) {
      setList(list.filter((item) => item !== genre));
      return;
    }
    setList([...list, genre]);
    if (other.includes(genre)) setOther(other.filter((item) => item !== genre));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!activeProfile || readOnly || pending) return;
    if (!preferred.length) {
      setError("Pick at least one genre you want more of.");
      return;
    }
    setPending(true);
    setError("");
    try {
      const updated = await api.saveOnboardingPreferences(activeProfile.id, preferred, avoid);
      const next = profiles.map((profile) =>
        profile.id === activeProfile.id
          ? {
              ...profile,
              seedPreferredGenres: updated.seedPreferredGenres,
              seedDislikedGenres: updated.seedDislikedGenres,
            }
          : profile,
      );
      persistProfiles(next);
      bumpFeed();
      flash("Starting recommendations from your picks");
      navigate("/onboarding");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save your preferences");
    } finally {
      setPending(false);
    }
  };

  if (!activeProfile) {
    return <Page eyebrow="Setup" title="Choose a profile" intro="Create or select a profile to continue." />;
  }

  return (
    <Page
      eyebrow="Quick setup"
      title={`What does ${activeProfile.name} like?`}
      intro="Answer a few questions first. We’ll recommend from these genres right away, then refine your taste as you swipe."
    >
      <form className="onboarding-questions card" onSubmit={submit}>
        <fieldset>
          <legend>Genres you want to see more of</legend>
          <p className="field-hint">Choose up to eight.</p>
          <div className="genre-chip-grid">
            {ONBOARDING_GENRES.map((genre) => (
              <button
                type="button"
                key={`want-${genre}`}
                className={`genre-chip ${preferred.includes(genre) ? "selected want" : ""}`}
                aria-pressed={preferred.includes(genre)}
                disabled={readOnly || (preferred.length >= 8 && !preferred.includes(genre))}
                onClick={() => toggle(genre, preferred, setPreferred, avoid, setAvoid)}
              >
                {genre}
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Genres to avoid (optional)</legend>
          <div className="genre-chip-grid">
            {ONBOARDING_GENRES.map((genre) => (
              <button
                type="button"
                key={`avoid-${genre}`}
                className={`genre-chip ${avoid.includes(genre) ? "selected avoid" : ""}`}
                aria-pressed={avoid.includes(genre)}
                disabled={readOnly || (avoid.length >= 8 && !avoid.includes(genre))}
                onClick={() => toggle(genre, avoid, setAvoid, preferred, setPreferred)}
              >
                {genre}
              </button>
            ))}
          </div>
        </fieldset>
        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="inline-form">
          <button className="button primary" type="submit" disabled={readOnly || pending || !preferred.length}>
            {pending ? "Saving…" : "Continue to Discover"}
          </button>
        </div>
      </form>
    </Page>
  );
}

function Onboarding({ activeProfile, profiles, persistProfiles, readOnly, flash, bumpFeed }: SharedProps) {
  const [index, setIndex] = useState(0);
  const [pending, setPending] = useState<SwipeAction | "undo" | "">("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<(PreferenceResult & { movie: Movie })[]>([]);
  const [dragX, setDragX] = useState(0);
  const dragStart = useRef<number | null>(null);
  const navigate = useNavigate();
  const movieState = useRemote(
    () => activeProfile ? api.discover(activeProfile.id) : Promise.resolve(demoMovies),
    [activeProfile?.id],
  );
  const deck = useMemo(() => {
    const source = movieState.data ?? demoMovies;
    return [...source].sort((a, b) => {
      const eraA = Math.floor((a.year || 1900) / 10) % 5;
      const eraB = Math.floor((b.year || 1900) / 10) % 5;
      return (a.genres[0] ?? "").localeCompare(b.genres[0] ?? "") || eraA - eraB || a.id - b.id;
    });
  }, [movieState.data]);
  const movie = deck[index];
  const finish = () => {
    if (!activeProfile) return;
    persistProfiles(profiles.map((profile) => profile.id === activeProfile.id ? { ...profile, onboarded: true } : profile));
    flash(history.length ? "Taste profile saved" : "You can add preferences any time");
    navigate("/");
  };
  const submit = async (action: SwipeAction) => {
    if (!activeProfile || !movie || pending || readOnly) return;
    setPending(action);
    setError("");
    try {
      const result = await api.preference(activeProfile.id, movie, action, crypto.randomUUID());
      setHistory((current) => [...current, { ...result, movie }]);
      setIndex((current) => current + 1);
      bumpFeed();
      const messages: Record<SwipeAction, string> = {
        liked: "Liked — recommendations updated",
        disliked: "Disliked — recommendations updated",
        love: "Loved — recommendations updated",
        watchlist: "Added to your want-to-watch list",
        watched: "Added to watch history without changing your taste",
        not_interested: "Marked not interested",
        skip: "Skipped for now",
      };
      flash(messages[action]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Couldn’t save that choice");
    } finally {
      setPending("");
      setDragX(0);
    }
  };
  const undo = async () => {
    const latest = history.at(-1);
    if (!activeProfile || !latest || pending) return;
    setPending("undo");
    setError("");
    try {
      await api.undoPreference(activeProfile.id, latest);
      setHistory((current) => current.slice(0, -1));
      setIndex((current) => Math.max(0, current - 1));
      bumpFeed();
      flash("Latest choice undone");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Couldn’t undo that choice");
    } finally {
      setPending("");
    }
  };
  const pointerDown = (event: ReactPointerEvent) => {
    dragStart.current = event.clientX;
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event: ReactPointerEvent) => {
    if (dragStart.current !== null) setDragX(event.clientX - dragStart.current);
  };
  const pointerUp = () => {
    dragStart.current = null;
    if (Math.abs(dragX) >= 90) void submit(dragX > 0 ? "liked" : "disliked");
    else setDragX(0);
  };
  return (
    <Page eyebrow={`Discover your taste · ${activeProfile?.name ?? "movie lover"}`} title="What do you enjoy?" intro="Right means like and left means dislike. Watched, skip, and wishlist stay separate so viewing history never implies enjoyment.">
      <div className="taste-progress"><span>{history.length} choices saved</span><progress max={Math.max(deck.length, 1)} value={index} aria-label="Onboarding progress" /><button className="button" onClick={undo} disabled={!history.length || !!pending}>Undo latest</button></div>
      {movieState.loading ? <Skeleton count={1} /> : !movie ? <Empty title="You’re caught up" text="There are no more onboarding movies in this selection." /> :
        <section className="swipe-stage" tabIndex={0} aria-label="Movie preference card. Use left and right arrow keys to dislike or like." onKeyDown={(event) => {
          if (event.key === "ArrowRight") void submit("liked");
          if (event.key === "ArrowLeft") void submit("disliked");
          if (event.key.toLowerCase() === "s") void submit("skip");
          if (event.key.toLowerCase() === "w") void submit("watchlist");
          if (event.key.toLowerCase() === "u") void undo();
        }}>
          <motion.article className="swipe-card" animate={{ x: dragX, rotate: dragX / 25 }} transition={{ type: "spring", stiffness: 420, damping: 32 }} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
            <Poster key={movie.id} movie={movie} large />
            <div className="swipe-copy"><p className="eyebrow">{movie.genres.join(" · ")}</p><h2>{movie.title} {movie.year ? <small>({movie.year})</small> : null}</h2><p>{movie.synopsis}</p>{dragX > 45 && <strong className="swipe-label like">Like</strong>}{dragX < -45 && <strong className="swipe-label dislike">Dislike</strong>}</div>
          </motion.article>
          <div className="swipe-actions">
            <button className="button dislike-button" disabled={!!pending || readOnly} onClick={() => submit("disliked")}>← Dislike</button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("skip")}>Skip <kbd>S</kbd></button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("watchlist")}>＋ Want to watch <kbd>W</kbd></button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("watched")}>✓ Watched</button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("love")}>♥ Love</button>
            <button className="button like-button" disabled={!!pending || readOnly} onClick={() => submit("liked")}>Like →</button>
          </div>
        </section>}
      {error && <div className="save-error" role="alert">{error}. Your card was not advanced; retry the action.</div>}
      <div className="sticky-action"><span>No minimum required</span><button className="button primary" onClick={finish} disabled={!activeProfile}>See my recommendations →</button></div>
    </Page>
  );
}

function discoveryBalanceCopy(discovery: number): string {
  if (discovery <= 2) return "Movies similar to the ones you chose.";
  if (discovery >= 98) return "A random mix from the catalog.";
  return `${Math.round(100 - discovery)}% similar to what you like, ${discovery}% random.`;
}

function Home(props: SharedProps) {
  const [discovery, setDiscovery] = useState(35);
  const { data, error, loading } = useRemote(
    () => api.recommendations(props.activeProfile ? [props.activeProfile.id] : [], discovery),
    [props.activeProfile?.id, discovery, props.feedRevision],
  );
  return (
    <Page eyebrow={`Curated for ${props.activeProfile?.name ?? "you"}`} title="Your next favorite is waiting" intro="A considered mix of familiar picks and discoveries, tuned to what you enjoy.">
      <section className="discovery card" aria-labelledby="discovery-title"><div><h2 id="discovery-title">Discovery balance</h2><p>{discoveryBalanceCopy(discovery)}</p></div><div className="range-wrap"><span>Familiar</span><input aria-label="Discovery balance" aria-valuetext={discoveryBalanceCopy(discovery)} type="range" min="0" max="100" value={discovery} onChange={(event) => setDiscovery(Number(event.target.value))} /><span>Discovery</span></div></section>
      <SectionTitle title="Top picks for you" action={<Link to="/search">Browse all →</Link>} />
      <AsyncMovies loading={loading} error={error} movies={data} {...props} />
      <WantToWatchPreview {...props} />
    </Page>
  );
}

function WantToWatchPreview(props: SharedProps) {
  const { data, error, loading } = useRemote(
    () => (props.activeProfile ? api.wishlist(props.activeProfile.id) : Promise.resolve([])),
    [props.activeProfile?.id, props.feedRevision],
  );
  if (loading || error || !data?.length) return null;
  const preview = data.slice(0, 4);
  return (
    <>
      <SectionTitle title="Want to watch" action={<Link to="/wishlist">View wishlist →</Link>} />
      <AsyncMovies loading={false} error="" movies={preview} {...props} />
    </>
  );
}

function Search(props: SharedProps) {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const genre = params.get("genre") ?? "All";
  const year = params.get("year") ?? "Any";
  const language = params.get("language") ?? "All";
  const runtime = params.get("runtime") ?? "Any";
  const [page, setPage] = useState(1);
  const [collected, setCollected] = useState<Movie[]>([]);
  useEffect(() => { setPage(1); setCollected([]); }, [query]);
  const { data, error, loading } = useRemote(() => api.movies(query, page, 48), [query, page]);
  useEffect(() => {
    if (!data) return;
    setCollected((current) => {
      const merged = page === 1 ? data : [...current, ...data];
      const seen = new Set<number>();
      return merged.filter((movie) => (seen.has(movie.id) ? false : (seen.add(movie.id), true)));
    });
  }, [data, page]);
  const catalog = collected.length ? collected : data;
  const hasLanguage = catalog?.some((movie) => Boolean(movie.language)) ?? false;
  const hasRuntime = catalog?.some((movie) => movie.runtime > 0) ?? false;
  const filtered = catalog?.filter((movie) => {
    const matchesYear = year === "Any" || (year === "2020s" ? movie.year >= 2020 : year === "2010s" ? movie.year >= 2010 && movie.year < 2020 : movie.year < 2010);
    return (genre === "All" || movie.genres.includes(genre)) && matchesYear && (!hasLanguage || language === "All" || movie.language === language) && (!hasRuntime || runtime === "Any" || (runtime === "< 2h" ? movie.runtime < 120 : movie.runtime >= 120));
  });
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); value ? next.set(key, value) : next.delete(key); setParams(next); };
  return (
    <Page eyebrow="Explore" title="Find something worth watching" intro="Search the catalog, then refine without losing your place.">
      <label className="searchbox large">⌕<span className="sr-only">Search by title</span><input aria-label="Search by title" autoFocus value={query} onChange={(event) => update("q", event.target.value)} placeholder="Search movies…" /></label>
      <div className="filters" aria-label="Search filters">
        <Filter label="Genre" value={genre} values={["All", "Drama", "Comedy", "Sci-Fi", "Animation", "Action", "Romance"]} onChange={(value) => update("genre", value)} />
        <Filter label="Year" value={year} values={["Any", "2020s", "2010s", "Before 2010"]} onChange={(value) => update("year", value)} />
        {hasLanguage && <Filter label="Language" value={language} values={["All", ...Array.from(new Set(catalog?.map((movie) => movie.language).filter(Boolean)))]} onChange={(value) => update("language", value)} />}
        {hasRuntime && <Filter label="Runtime" value={runtime} values={["Any", "< 2h", "2h+"]} onChange={(value) => update("runtime", value)} />}
      </div>
      <p className="result-count">{loading && page === 1 ? "Searching…" : `${filtered?.length ?? 0} results`}</p>
      <AsyncMovies loading={loading && page === 1} error={error} movies={filtered} {...props} />
      {data && data.length >= 48 && <div className="sticky-action"><button className="button" onClick={() => setPage((current) => current + 1)} disabled={loading}>Show more movies</button></div>}
    </Page>
  );
}

function MovieDetail(props: SharedProps) {
  const id = Number(useParams().id);
  const movieState = useRemote(() => api.movie(id), [id]);
  const similarState = useRemote(() => api.similar(id), [id]);
  if (movieState.loading) return <Page title="Loading movie…"><Skeleton count={1} /></Page>;
  if (movieState.error || !movieState.data) return <Page title="We couldn't load this movie"><ErrorState message={movieState.error} /></Page>;
  const movie = movieState.data;
  return (
    <Page title="">
      <div className="detail">
        <Poster movie={movie} large />
        <div><Link to="/search" className="back">← Back to search</Link><p className="eyebrow">{movie.genres.join(" · ")}</p><h1>{movie.title}</h1><p className="meta">{[movie.year || null, movie.runtime ? `${movie.runtime} min` : null, movie.language || null].filter(Boolean).join(" · ")}</p><p className="synopsis">{movie.synopsis}</p>{movie.reason && <div className="match-reason"><span>{movie.score}% match</span><p>{movie.reason}</p></div>}<MovieActions movie={movie} {...props} /></div>
      </div>
      <SectionTitle title="More like this" />
      <AsyncMovies loading={similarState.loading} error={similarState.error} movies={similarState.data} {...props} />
    </Page>
  );
}

function Group(props: SharedProps) {
  const location = useLocation();
  const highlightedRoomId = (location.state as { roomId?: number } | null)?.roomId;
  const [refresh, setRefresh] = useState(0);
  const groupState = useRemote(api.groups, [refresh]);
  const [selectedId, setSelectedId] = useState<number>();
  const [name, setName] = useState("");
  const [inviteInfo, setInviteInfo] = useState<{ id: number; url: string; roomName: string }>();
  const [strategy, setStrategy] = useState<"balanced" | "everyone_matters">("balanced");
  const [includeWatched, setIncludeWatched] = useState(false);
  const [sessionRevision, setSessionRevision] = useState(0);
  const groups = groupState.data ?? [];
  const selected = groups.find((group) => group.id === selectedId) ?? groups[0];
  const roomState = useRemote(
    () => selected && props.activeProfile
      ? api.groupRooms(selected.id, props.activeProfile.id)
      : Promise.resolve([]),
    [selected?.id, props.activeProfile?.id, refresh, sessionRevision],
  );
  const rooms = roomState.data ?? [];
  const sessionListState = useRemote(
    () => selected && props.activeProfile
      ? api.groupSessions(selected.id, props.activeProfile.id)
      : Promise.resolve([]),
    [selected?.id, props.activeProfile?.id, sessionRevision],
  );
  const legacySession = sessionListState.data?.find((session) => session.status === "active" && session.roomId == null);
  const sessionMovieState = useRemote(
    () => legacySession && props.activeProfile
      ? api.sessionRecommendations(legacySession.id, props.activeProfile.id)
      : Promise.resolve([]),
    [legacySession?.id, props.activeProfile?.id, sessionRevision],
  );
  const showSharedList = !roomState.loading && rooms.length === 0 && (selected?.members.length ?? 0) >= 2;
  const { data, error, loading } = useRemote(
    () => showSharedList && selected && props.activeProfile ? api.groupRecommendations(selected.id, props.activeProfile.id, strategy, includeWatched) : Promise.resolve([]),
    [showSharedList, selected?.id, props.activeProfile?.id, strategy, includeWatched, selected?.recommendationVersion, props.feedRevision],
  );
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!props.activeProfile || !name.trim()) return;
    try {
      const group = await api.createGroup(name.trim(), props.activeProfile.id);
      setName("");
      setSelectedId(group.id);
      setRefresh((value) => value + 1);
      props.flash("Group created");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t create group");
    }
  };
  const invite = async () => {
    if (!selected || !props.activeProfile) return;
    try {
      const result = await api.createInvite(selected.id, props.activeProfile.id);
      setInviteInfo({ id: result.id, url: result.invite_url, roomName: result.room_name });
      setRefresh((value) => value + 1);
      let copied = false;
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(result.invite_url);
          copied = true;
        }
      } catch {
        copied = false;
      }
      props.flash(copied
        ? `${result.room_name} link copied. It expires in 48 hours.`
        : `${result.room_name} link created. Copy it below.`);
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t create invitation");
    }
  };
  const revokeInvite = async () => {
    if (!selected || !props.activeProfile || !inviteInfo) return;
    try {
      await api.revokeInvite(selected.id, inviteInfo.id, props.activeProfile.id);
      setInviteInfo(undefined);
      props.flash("Invitation revoked");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t revoke invitation");
    }
  };
  const removeMember = async (memberProfileId: number) => {
    if (!selected || !props.activeProfile) return;
    try {
      await api.removeGroupMember(selected.id, memberProfileId, props.activeProfile.id);
      setRefresh((value) => value + 1);
      props.flash(memberProfileId === props.activeProfile.id ? "You left the group" : "Member removed");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t update membership");
    }
  };
  const startRoom = async (room: GroupRoom, mode: GroupRecommendationMode) => {
    if (!props.activeProfile) return;
    try {
      await api.createGroupSession(
        room.groupId,
        props.activeProfile.id,
        room.members.map((member) => member.profileId),
        room.id,
        mode,
      );
      setSessionRevision((value) => value + 1);
      props.flash(`${groupModeTitle(mode)} movie night started in ${room.name}`);
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t start session");
    }
  };
  const vote = async (movie: Movie, action: "like" | "love" | "dislike" | "veto") => {
    if (!legacySession || !props.activeProfile) return;
    try {
      await api.groupVote(legacySession.id, props.activeProfile.id, movie.id, action);
      setSessionRevision((value) => value + 1);
      props.flash("Group vote saved");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t save vote");
    }
  };
  return (
    <Page eyebrow="Movie night" title="Find a film everyone can enjoy" intro="Each invitation link opens its own room. People who join a different person’s link show up in that room, with their own movie night.">
      {!props.activeProfile ? <Empty title="Create your profile first" text="A profile is needed to own or join a group." action={<Link className="button primary" to="/profiles">Create profile</Link>} /> : <>
        <form className="inline-form group-create card" onSubmit={create}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="New group name" aria-label="New group name" required /><button className="button primary">Create group</button></form>
        {groupState.loading ? <Skeleton count={2} /> : groupState.error ? <ErrorState message={groupState.error} /> : !groups.length ? <Empty title="No groups yet" text="Create one, then generate an invitation link. That link becomes its own room." /> : <>
          <div className="group-tabs">{groups.map((group) => <button className={`button ${group.id === selected?.id ? "primary" : ""}`} key={group.id} onClick={() => { setSelectedId(group.id); setInviteInfo(undefined); }}>{group.name}</button>)}</div>
          {selected && <section className="group-summary card"><div><h2>{selected.name}</h2><p>{selected.members.length} member{selected.members.length === 1 ? "" : "s"}</p></div><button className="button" onClick={invite}>Create room link</button>{inviteInfo && <div className="invite-link"><label><span>Share this link for {inviteInfo.roomName}</span><input readOnly value={inviteInfo.url} onFocus={(event) => event.currentTarget.select()} /></label><button className="button" onClick={revokeInvite}>Revoke invitation</button></div>}<div className="member-list">{selected.members.map((member) => <span key={member.profileId}>{member.name} · {member.needsOnboarding ? "needs more onboarding" : `${member.feedbackCount} preferences`}{((selected.ownerProfileId === props.activeProfile?.id && member.profileId !== selected.ownerProfileId) || (member.profileId === props.activeProfile?.id && member.profileId !== selected.ownerProfileId)) && <button onClick={() => removeMember(member.profileId)}>{member.profileId === props.activeProfile?.id ? "Leave" : "Remove"}</button>}</span>)}</div></section>}
          <label className="strategy-row card"><span><strong>Ranking strategy</strong><small>Balanced emphasizes average enjoyment; Everyone matters emphasizes the least-satisfied member.</small></span><select value={strategy} onChange={(event) => setStrategy(event.target.value as typeof strategy)}><option value="balanced">Balanced</option><option value="everyone_matters">Everyone matters</option></select></label>
          <label className="switch-row card"><span><strong>Include already watched movies</strong><small>Off by default so nobody gets a repeat.</small></span><input type="checkbox" role="switch" checked={includeWatched} onChange={(event) => setIncludeWatched(event.target.checked)} /></label>
          {roomState.loading ? <Skeleton count={2} /> : roomState.error ? <ErrorState message={roomState.error} /> : rooms.length > 0 ? <div className="room-list">{selected?.ownerProfileId === props.activeProfile?.id && <p className="compromise-note">You own this group, so every room is listed. Friends only see the room for the link they opened.</p>}{rooms.map((room) => <RoomPanel key={room.id} room={room} groupOwnerProfileId={selected?.ownerProfileId} session={(sessionListState.data ?? []).find((session) => session.status === "active" && session.roomId === room.id)} strategy={strategy} includeWatched={includeWatched} sessionRevision={sessionRevision} highlighted={room.id === highlightedRoomId} onStart={startRoom} onChanged={() => { setSessionRevision((value) => value + 1); setRefresh((value) => value + 1); }} {...props} />)}</div> : selected && selected.members.length < 2 ? <Empty title="Waiting for a room" text="Create a room link. Anyone who opens a different person’s link joins a different room." /> : null}
          {legacySession && <section className="card group-session-panel"><p className="eyebrow">Live group session</p><h2>Consensus feed</h2><p>Every participant has one vote. A veto removes that movie from this session only.</p>{sessionMovieState.loading ? <Skeleton count={1} /> : sessionMovieState.data?.[0] && <><MovieCard movie={sessionMovieState.data[0]} {...props} /><div className="swipe-actions"><button className="button dislike-button" onClick={() => vote(sessionMovieState.data![0], "dislike")}>Dislike</button><button className="button" onClick={() => vote(sessionMovieState.data![0], "veto")}>Veto</button><button className="button" onClick={() => vote(sessionMovieState.data![0], "love")}>Love</button><button className="button like-button" onClick={() => vote(sessionMovieState.data![0], "like")}>Like</button></div></>}</section>}
          {showSharedList && selected && selected.members.length >= 2 && <><p className="compromise-note">Group scores are estimates. Conflicts are labeled “Best available compromise”; no result promises everyone will like it.</p><AsyncMovies loading={loading} error={error} movies={data} {...props} /></>}
        </>}
      </>}
    </Page>
  );
}

function RoomPanel({
  room, groupOwnerProfileId, session, strategy, includeWatched, sessionRevision, highlighted, onStart, onChanged, ...props
}: {
  room: GroupRoom;
  groupOwnerProfileId?: number;
  session?: GroupSession;
  strategy: "balanced" | "everyone_matters";
  includeWatched: boolean;
  sessionRevision: number;
  highlighted: boolean;
  onStart: (room: GroupRoom, mode: GroupRecommendationMode) => void;
  onChanged: () => void;
} & SharedProps) {
  const [recommendationMode, setRecommendationMode] =
    useState<GroupRecommendationMode>(session?.rankingAlgorithm ?? "fair_consensus");
  const [updatingMode, setUpdatingMode] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [removed, setRemoved] = useState(false);
  const inRoom = room.members.some((member) => member.profileId === props.activeProfile?.id);
  const isHost = props.activeProfile?.id === room.hostProfileId;
  const canDeleteRoom =
    !!props.activeProfile &&
    (isHost || props.activeProfile.id === groupOwnerProfileId);
  const participating =
    !removed &&
    !deleting &&
    !!session &&
    !!props.activeProfile &&
    session.participantProfileIds.includes(props.activeProfile.id);
  const movieState = useRemote(
    () => participating && props.activeProfile
      ? api.sessionRecommendations(session.id, props.activeProfile.id)
      : Promise.resolve([]),
    [participating, session?.id, session?.rankingAlgorithm, props.activeProfile?.id, sessionRevision, removed, deleting],
  );
  const ranked = useRemote(
    () => !removed && !deleting && room.members.length >= 2 && props.activeProfile
      ? api.roomRecommendations(room.groupId, room.id, props.activeProfile.id, strategy, includeWatched)
      : Promise.resolve([]),
    [removed, deleting, room.groupId, room.id, room.members.length, props.activeProfile?.id, strategy, includeWatched, props.feedRevision],
  );
  useEffect(() => {
    if (session?.rankingAlgorithm) setRecommendationMode(session.rankingAlgorithm);
  }, [session?.id, session?.rankingAlgorithm]);
  const activeSession = session?.status === "active" ? session : undefined;
  const modeDirty = activeSession
    ? recommendationMode !== activeSession.rankingAlgorithm
    : true;
  const applyRecommendationMode = async () => {
    if (!props.activeProfile) return;
    if (!activeSession) {
      onStart(room, recommendationMode);
      return;
    }
    if (!modeDirty) {
      props.flash(`Already using ${groupModeTitle(recommendationMode)}`);
      return;
    }
    setUpdatingMode(true);
    try {
      await api.updateGroupSessionRanking(activeSession.id, props.activeProfile.id, recommendationMode);
      onChanged();
      props.flash(`Recommendation mode updated to ${groupModeTitle(recommendationMode)}`);
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t update recommendation mode");
    } finally {
      setUpdatingMode(false);
    }
  };
  const vote = async (movie: Movie, action: "like" | "love" | "dislike" | "veto") => {
    if (!session || !props.activeProfile) return;
    try {
      await api.groupVote(session.id, props.activeProfile.id, movie.id, action);
      onChanged();
      props.flash("Group vote saved");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t save vote");
    }
  };
  const copyRoomLink = async () => {
    if (!props.activeProfile) return;
    try {
      const link = await api.roomInviteLink(room.groupId, room.id, props.activeProfile.id);
      let copied = false;
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(link.invite_url);
          copied = true;
        }
      } catch {
        copied = false;
      }
      props.flash(copied ? `${room.name} link copied` : `${room.name} link ready — copy from the address bar if needed`);
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t copy room link");
    }
  };
  const deleteRoom = async () => {
    if (!props.activeProfile || !window.confirm(`Delete ${room.name}? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await api.deleteRoom(room.groupId, room.id, props.activeProfile.id);
      setRemoved(true);
      onChanged();
      props.flash(`${room.name} deleted`);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 404) {
        setRemoved(true);
        onChanged();
        props.flash(`${room.name} deleted`);
        return;
      }
      props.flash(reason instanceof Error ? reason.message : "Couldn’t delete room");
    } finally {
      setDeleting(false);
    }
  };
  if (removed) return null;
  const removeMember = async (memberProfileId: number) => {
    if (!props.activeProfile) return;
    try {
      await api.removeRoomMember(room.groupId, room.id, memberProfileId, props.activeProfile.id);
      onChanged();
      props.flash(memberProfileId === props.activeProfile.id ? "You left the room" : "Removed from room");
    } catch (reason) {
      props.flash(reason instanceof Error ? reason.message : "Couldn’t update room membership");
    }
  };
  return (
    <article className={`card room-card ${highlighted ? "highlighted" : ""}`}>
      <p className="eyebrow">Room</p>
      <h2>{room.name}</h2>
      <p>Hosted by {room.hostName}. Only people who opened this link are in this room.</p>
      {isHost && <div className="room-actions"><button className="button" type="button" onClick={copyRoomLink}>Copy room link</button>{canDeleteRoom && <button className="button danger-button" type="button" onClick={deleteRoom} disabled={deleting}>{deleting ? "Deleting…" : "Delete room"}</button>}</div>}
      {!isHost && canDeleteRoom && <div className="room-actions"><button className="button danger-button" type="button" onClick={deleteRoom} disabled={deleting}>{deleting ? "Deleting…" : "Delete room"}</button></div>}
      <div className="member-list">{room.members.map((member) => <span key={member.profileId}>{member.name}{member.host ? " · host" : ""}{(isHost && !member.host) || (member.profileId === props.activeProfile?.id && !member.host) ? <button type="button" onClick={() => removeMember(member.profileId)}>{member.profileId === props.activeProfile?.id ? "Leave" : "Remove"}</button> : null}</span>)}</div>
      {isHost && <RecommendationModePicker
        roomId={room.id}
        value={recommendationMode}
        onChange={setRecommendationMode}
        actionLabel={activeSession
          ? (modeDirty ? `Apply ${groupModeTitle(recommendationMode)}` : "Recommendation mode saved")
          : `Start with ${groupModeTitle(recommendationMode)}`}
        onAction={applyRecommendationMode}
        actionDisabled={!activeSession && room.members.length < 2}
        actionPending={updatingMode}
      />}
      {!isHost && inRoom && !activeSession && <p className="compromise-note">Waiting for {room.hostName} to choose a recommendation mode and start movie night.</p>}
      {!inRoom && <p>You can see this room because you own the group.</p>}
      {participating && session && <div className="group-session-panel"><h3>{groupModeTitle(session.rankingAlgorithm)} consensus feed</h3><p>Mode: <strong>{groupModeTitle(session.rankingAlgorithm)}</strong>. Every participant has one vote; a veto removes that movie from this room only.</p>{movieState.loading ? <Skeleton count={1} /> : movieState.data?.[0] && <><MovieCard movie={movieState.data[0]} {...props} /><div className="swipe-actions"><button className="button dislike-button" onClick={() => vote(movieState.data![0], "dislike")}>Dislike</button><button className="button" onClick={() => vote(movieState.data![0], "veto")}>Veto</button><button className="button" onClick={() => vote(movieState.data![0], "love")}>Love</button><button className="button like-button" onClick={() => vote(movieState.data![0], "like")}>Like</button></div></>}</div>}
      {room.members.length < 2 ? <p className="compromise-note">Waiting for someone to open this link.</p> : <><p className="compromise-note">Scores in this room use only these members.</p><AsyncMovies loading={ranked.loading} error={ranked.error} movies={ranked.data} {...props} /></>}
    </article>
  );
}

function JoinInvite(props: SharedProps) {
  const token = useParams().token ?? "";
  const [state, setState] = useState<"idle" | "joining" | "joined" | "error">("idle");
  const [message, setMessage] = useState("");
  const navigate = useNavigate();
  const join = async () => {
    if (!props.activeProfile) return;
    setState("joining");
    try {
      const joined = await api.joinGroup(token, props.activeProfile.id);
      setState("joined");
      setMessage(joined.roomName ? `Joined ${joined.roomName}` : `Joined ${joined.group.name}`);
      window.setTimeout(() => navigate("/group", { state: { roomId: joined.roomId } }), 800);
    } catch (reason) {
      setState("error");
      setMessage(reason instanceof Error ? reason.message : "This invitation is invalid or expired.");
    }
  };
  return <Page eyebrow="Group invitation" title="Join movie night" intro="Your ratings and swipe choices remain private. The group only receives aggregate recommendation scores.">
    {!props.activeProfile ? <Empty title="Choose an owned profile" text="Create or select your profile before joining." action={<Link className="button primary" to="/profiles">Manage profiles</Link>} /> :
      <div className="state"><span>◇</span><h2>{state === "joined" ? message : "Ready to join?"}</h2>{state === "error" && <p role="alert">{message}</p>}<button className="button primary" onClick={join} disabled={state === "joining" || state === "joined"}>{state === "joining" ? "Joining…" : "Join group"}</button></div>}
  </Page>;
}

function WishlistView(props: SharedProps) {
  const { activeProfile, readOnly, flash, bumpFeed } = props;
  const { data, error, loading } = useRemote(
    () => (activeProfile ? api.wishlist(activeProfile.id) : Promise.resolve([])),
    [activeProfile?.id, props.feedRevision],
  );
  const remove = async (movie: Movie) => {
    if (!activeProfile || readOnly) return;
    try {
      await api.removeFromWishlist(activeProfile.id, movie.id);
      bumpFeed();
      flash(`Removed ${movie.title} from want to watch`);
    } catch {
      flash("Couldn’t update your wishlist");
    }
  };
  if (!activeProfile) {
    return (
      <Page eyebrow="Your wishlist" title="Want to watch">
        <Empty title="Choose a profile" text="Select or create a profile to see saved movies." action={<Link className="button primary" to="/profiles">Manage profiles</Link>} />
      </Page>
    );
  }
  return (
    <Page eyebrow="Your wishlist" title="Want to watch" intro={`Movies ${activeProfile.name} plans to watch. Adding here does not mark them as watched or liked.`}>
      {loading ? <Skeleton count={8} /> : error ? <ErrorState message={error} /> : !data?.length ? (
        <Empty title="Nothing saved yet" text="Use Want to watch on Discover, search, or any movie card to build your list." action={<Link className="button primary" to="/onboarding">Discover movies</Link>} />
      ) : (
        <div className="movie-grid wishlist-grid">
          {data.map((movie) => (
            <article className="movie-card" key={movie.id}>
              <Link to={`/movies/${movie.id}`} aria-label={`View ${movie.title}`}><Poster movie={movie} /></Link>
              <div className="movie-copy">
                <p className="movie-genres">{movie.genres.join(" · ")}</p>
                <h3><Link to={`/movies/${movie.id}`}>{movie.title}</Link></h3>
                <p>{[movie.year || null, movie.runtime ? `${movie.runtime} min` : null].filter(Boolean).join(" · ")}</p>
                <div className="movie-actions compact-actions">
                  <button className="button quiet" disabled={readOnly} onClick={() => void remove(movie)} aria-label={`Remove ${movie.title} from want to watch`}>Remove</button>
                  <Link className="button" to={`/movies/${movie.id}`}>Details</Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </Page>
  );
}

function SettingsView({ setReadOnly, user, setUser, logout, bumpFeed }: { setReadOnly: (value: boolean) => void; user: User; setUser: (user: User) => void; logout: () => void; bumpFeed: () => void }) {
  const { data, error } = useRemote(api.settings);
  const [username, setUsername] = useState(user.username);
  const [email, setEmail] = useState(user.email);
  const [newPassword, setNewPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [challenge, setChallenge] = useState<{ purpose: "username" | "email" | "password"; sentTo: string; previewCode?: string }>();
  const [message, setMessage] = useState("");
  const readOnly = data?.readOnly ?? false;
  useEffect(() => { if (data) setReadOnly(data.readOnly); }, [data, setReadOnly]);
  useEffect(() => {
    setUsername(user.username);
    setEmail(user.email);
  }, [user.username, user.email]);
  const sendCode = async (event: FormEvent, purpose: "username" | "email" | "password") => {
    event.preventDefault();
    if (!currentPassword) {
      setMessage("Enter your current password before requesting a code.");
      return;
    }
    setPending(purpose);
    setMessage("");
    try {
      const issued = await api.requestVerification({
        purpose,
        current_password: currentPassword,
        ...(purpose === "username" ? { username: username.trim() } : {}),
        ...(purpose === "email" ? { email: email.trim() } : {}),
        ...(purpose === "password" ? { password: newPassword } : {}),
      });
      setChallenge({
        purpose,
        sentTo: issued.sent_to,
        previewCode: issued.preview_code || undefined,
      });
      setCode("");
      setMessage(issued.delivery === "email"
        ? `We sent a 6-digit code to ${issued.sent_to}.`
        : `Mail is not configured, so the code for ${issued.sent_to} is shown below.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Couldn’t send a verification code");
    } finally {
      setPending("");
    }
  };
  const confirmChange = async (event: FormEvent) => {
    event.preventDefault();
    if (!challenge) return;
    setPending("confirm");
    try {
      const updated = await api.confirmVerification(challenge.purpose, code.trim());
      setUser(updated);
      setChallenge(undefined);
      setCode("");
      setNewPassword("");
      setCurrentPassword("");
      setMessage(`Your ${challenge.purpose} was updated.`);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Couldn’t confirm that code");
    } finally {
      setPending("");
    }
  };
  const reset = async () => {
    setPending("reset");
    try {
      await api.resetRecommendations();
      bumpFeed();
      setConfirmReset(false);
      setMessage("Personal taste feedback reset. Watch history and wishlist were preserved.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Couldn’t reset recommendations");
    } finally {
      setPending("");
    }
  };
  return (
    <Page eyebrow="System" title="Settings" intro="Update your account and reset recommendations.">
      {error && <ErrorState message={error} />}
      <section className="card account-card">
        <h2>Account</h2>
        <p>Signed in as {user.username}. Username and password codes go to your current email. An email change is confirmed at the new address.</p>
        <label>Current password<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
        <form onSubmit={(event) => sendCode(event, "username")}>
          <label>Username<input value={username} autoComplete="username" minLength={3} maxLength={80} onChange={(event) => setUsername(event.target.value)} required /></label>
          <button className="button" disabled={readOnly || pending === "username"}>Email a code to change username</button>
        </form>
        <form onSubmit={(event) => sendCode(event, "email")}>
          <label>Email<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <button className="button" disabled={readOnly || pending === "email"}>Email a code to change email</button>
        </form>
        <form onSubmit={(event) => sendCode(event, "password")}>
          <label>New password<input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label>
          <button className="button" disabled={readOnly || pending === "password"}>Email a code to change password</button>
        </form>
        {challenge && <form onSubmit={confirmChange}>
          <p>Enter the code for your {challenge.purpose} change. It was sent to {challenge.sentTo}.</p>
          {challenge.previewCode && <p className="preview-code">Verification code: <strong>{challenge.previewCode}</strong></p>}
          <label>Verification code<input inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value)} required /></label>
          <button className="button primary" disabled={readOnly || pending === "confirm"}>Confirm change</button>
        </form>}
        <button className="button" type="button" onClick={logout}>Log out</button>
      </section>
      <section className="card account-card">
        <h2>Recommendation controls</h2>
        <p>Reset likes and dislikes without deleting your account, wishlist, or watch history.</p>
        {confirmReset ? <div className="account-actions"><button className="button danger-button" onClick={reset} disabled={readOnly || pending === "reset"}>Confirm reset</button><button className="button" type="button" onClick={() => setConfirmReset(false)}>Cancel</button></div> : <button className="button" type="button" onClick={() => setConfirmReset(true)} disabled={readOnly}>Reset recommendations</button>}
      </section>
      {message && <p role="status">{message}</p>}
    </Page>
  );
}

function AsyncMovies({ loading, error, movies, ...props }: { loading: boolean; error: string; movies?: Movie[] } & SharedProps) {
  if (loading) return <Skeleton count={12} />;
  if (error) return <ErrorState message={error} />;
  if (!movies?.length) return <Empty title="Nothing here yet" text="Adjust your search or filters and try again." />;
  return <div className="movie-grid">{movies.map((movie) => <MovieCard key={movie.id} movie={movie} {...props} />)}</div>;
}

function MovieCard({ movie, ...props }: { movie: Movie } & SharedProps) {
  return <article className="movie-card"><Link to={`/movies/${movie.id}`} aria-label={`View ${movie.title}`}><Poster movie={movie} />{movie.score > 0 && <div className="score">{movie.score}%</div>}</Link><div className="movie-copy"><p className="movie-genres">{(movie.genres ?? []).join(" · ")}</p><h3><Link to={`/movies/${movie.id}`}>{movie.title}</Link></h3><p>{[movie.year || null, movie.runtime ? `${movie.runtime} min` : null].filter(Boolean).join(" · ")}</p>{movie.reason && <small>{movie.reason}</small>}<MovieActions movie={movie} {...props} compact /></div></article>;
}

function MovieActions({ movie, activeProfile, readOnly, flash, bumpFeed, compact = false }: { movie: Movie; compact?: boolean } & SharedProps) {
  const [pending, setPending] = useState("");
  const act = async (action: MovieAction) => {
    if (!activeProfile || readOnly) return;
    setPending(action);
    try {
      if (action === "watchlist") {
        await api.addToWishlist(activeProfile.id, movie);
      } else {
        await api.act(activeProfile.id, movie.id, action);
      }
      bumpFeed();
      flash(action === "watchlist" ? "Added to want to watch" : action === "watched" ? "Marked as watched" : "Hidden from recommendations");
    } catch { flash("Couldn’t save that change"); } finally { setPending(""); }
  };
  return <div className={`movie-actions ${compact ? "compact-actions" : ""}`}><button disabled={readOnly || !!pending} onClick={() => act("watchlist")} aria-label={`Add ${movie.title} to want to watch`}>＋ <span>Want to watch</span></button><button disabled={readOnly || !!pending} onClick={() => act("watched")} aria-label={`Mark ${movie.title} watched`}>✓ <span>Watched</span></button><button disabled={readOnly || !!pending} onClick={() => act("dismiss")} aria-label={`Dismiss ${movie.title}`}>× <span>Not for me</span></button></div>;
}

function RatingCard({ movie, value, setValue, disabled }: { movie: Movie; value?: number; setValue: (rating: number) => void; disabled: boolean }) {
  return <article className="rating-card"><Poster movie={movie} /><div><h3>{movie.title}</h3><p>{movie.year} · {movie.genres[0]}</p><fieldset className="stars" disabled={disabled}><legend className="sr-only">Rate {movie.title}</legend>{[1, 2, 3, 4, 5].map((rating) => <button type="button" key={rating} className={rating <= (value ?? 0) ? "filled" : ""} onClick={() => setValue(rating)} aria-label={`${rating} star${rating > 1 ? "s" : ""}`}>★</button>)}</fieldset></div></article>;
}

function Poster({ movie, large = false }: { movie: Movie; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [movie.id, movie.posterUrl]);
  const initials = movie.title.split(" ").filter((word) => !["the", "of", "a"].includes(word.toLowerCase())).slice(0, 2).map((word) => word[0]).join("");
  const showImage = Boolean(movie.posterUrl) && !failed;
  return <div className={`poster ${large ? "poster-large" : ""} ${showImage ? "has-image" : ""}`}>
    {showImage
      ? <img src={movie.posterUrl} alt={`${movie.title}${movie.year ? ` (${movie.year})` : ""} poster`} loading={large ? "eager" : "lazy"} decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
      : <div className="poster-fallback" role="img" aria-label={`Poster unavailable for ${movie.title}`}><span>{initials}</span><i>{movie.genres[0] ?? "Movie"}</i></div>}
  </div>;
}

function Page({ eyebrow, title, intro, children }: { eyebrow?: string; title: string; intro?: string; children?: ReactNode }) {
  return <div className="page"><header className="page-title">{eyebrow && <p className="eyebrow">{eyebrow}</p>}{title && <h1>{title}</h1>}{intro && <p>{intro}</p>}</header>{children}</div>;
}
function SectionTitle({ title, action }: { title: string; action?: ReactNode }) { return <div className="section-title"><h2>{title}</h2>{action}</div>; }
function Filter({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) { return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{values.map((item) => <option key={item}>{item}</option>)}</select></label>; }
function Skeleton({ count }: { count: number }) { return <div className="movie-grid" aria-label="Loading"><span className="sr-only">Loading results</span>{Array.from({ length: count }, (_, index) => <div className="skeleton" key={index}><i /><b /><b /></div>)}</div>; }
function ErrorState({ message }: { message: string }) { return <div className="state error" role="alert"><span>!</span><h2>Something went wrong</h2><p>{message || "Please try again shortly."}</p><button className="button" onClick={() => location.reload()}>Try again</button></div>; }
function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) { return <div className="state"><span>◇</span><h2>{title}</h2><p>{text}</p>{action}</div>; }
function NotFound() { return <Page title="That page wandered off"><Empty title="404" text="Let’s get you back to something worth watching." action={<Link className="button primary" to="/">Go home</Link>} /></Page>; }
function Footer() { return <footer><span>CineMatch</span><p>Recommendations with a reason. This product uses the TMDB API but is not endorsed or certified by TMDB. Streaming availability data is provided by JustWatch.</p><Link to="/profiles">Profiles</Link><Link to="/settings">Settings</Link></footer>; }

export default App;
