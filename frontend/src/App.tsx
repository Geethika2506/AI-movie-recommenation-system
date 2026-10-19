import { FormEvent, PointerEvent as ReactPointerEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "./api";
import { movies as demoMovies } from "./data";
import type { Evaluation, Movie, MovieAction, PreferenceResult, Profile, Settings, SwipeAction } from "./types";

const colors = ["#f6b84b", "#77cbb9", "#dd7d91", "#9b8ee8"];

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
  const [profiles, setProfiles] = useState<Profile[]>(() => {
    try { return JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]") as Profile[]; } catch { return []; }
  });
  const [activeId, setActiveId] = useState(() => Number(localStorage.getItem("cinematch:activeProfile") ?? 0));
  const [readOnly, setReadOnly] = useState(false);
  const [notice, setNotice] = useState("");
  const [feedRevision, setFeedRevision] = useState(0);
  const activeProfile = profiles.find((profile) => profile.id === activeId) ?? profiles[0];
  const bumpFeed = () => setFeedRevision((value) => value + 1);
  useEffect(() => {
    let current = true;
    const syncProfiles = async () => {
      const local = (() => {
        try { return JSON.parse(localStorage.getItem("cinematch:profiles") ?? "[]") as Profile[]; } catch { return []; }
      })();
      const next: Profile[] = [];
      if (local.length) {
        for (const profile of local) {
          if (profile.id > 0) {
            next.push(profile);
            continue;
          }
          try {
            const created = await api.createProfile(profile.name);
            next.push({ ...created, color: profile.color, onboarded: profile.onboarded });
          } catch {
            try {
              const remote = await api.profiles();
              const match = remote.find((item) => item.name === profile.name);
              next.push(match ? { ...match, color: profile.color, onboarded: profile.onboarded } : profile);
            } catch {
              next.push(profile);
            }
          }
        }
      } else {
        try { next.push(...await api.profiles()); } catch { return; }
      }
      if (!current || !next.length) return;
      setProfiles(next);
      api.saveProfiles(next);
      const preferred = next.find((profile) => profile.id === activeId) ?? next[0];
      if (preferred) {
        setActiveId(preferred.id);
        localStorage.setItem("cinematch:activeProfile", String(preferred.id));
      }
    };
    void syncProfiles();
    return () => { current = false; };
  }, []);
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
  const app = { profiles, activeProfile, persistProfiles, selectProfile, readOnly, setReadOnly, flash, feedRevision, bumpFeed };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">Skip to content</a>
      <Header profiles={profiles} activeProfile={activeProfile} selectProfile={selectProfile} />
      {readOnly && <div className="readonly-banner" role="status">Read-only mode — browsing works, but changes are disabled.</div>}
      {notice && <div className="toast" role="status">{notice}</div>}
      <main id="content">
        <Routes>
          <Route path="/" element={profiles.length ? <Home {...app} /> : <Profiles {...app} />} />
          <Route path="/profiles" element={<Profiles {...app} />} />
          <Route path="/onboarding" element={<Onboarding {...app} />} />
          <Route path="/search" element={<Search {...app} />} />
          <Route path="/movies/:id" element={<MovieDetail {...app} />} />
          <Route path="/group" element={<Group {...app} />} />
          <Route path="/invite/:token" element={<JoinInvite {...app} />} />
          <Route path="/evaluation" element={<EvaluationView />} />
          <Route path="/settings" element={<SettingsView setReadOnly={setReadOnly} />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
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

function Header({ profiles, activeProfile, selectProfile }: Pick<SharedProps, "profiles" | "activeProfile" | "selectProfile">) {
  return (
    <header className="header">
      <Link to="/" className="brand" aria-label="CineMatch home"><span>Cine</span>Match<i /></Link>
      <nav aria-label="Main navigation">
        <NavLink to="/">For you</NavLink><NavLink to="/onboarding">Discover</NavLink><NavLink to="/search">Search</NavLink><NavLink to="/group">Group</NavLink><NavLink to="/evaluation">Evaluation</NavLink>
      </nav>
      <div className="header-actions">
        <Link className="icon-button" to="/settings" aria-label="Settings">⚙</Link>
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
    navigate("/onboarding");
  };
  return (
    <Page eyebrow="Your space" title={profiles.length ? "Who's watching?" : "Let’s make this yours"} intro="Separate profiles keep every recommendation personal. Everything stays local until a backend is connected.">
      {profiles.length > 0 && <div className="profile-grid">{profiles.map((profile) => (
        <button className={`profile-card ${profile.id === activeProfile?.id ? "active" : ""}`} key={profile.id} onClick={() => { selectProfile(profile.id); navigate(profile.onboarded ? "/" : "/onboarding"); }}>
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
      const result = await api.preference(activeProfile.id, movie.id, action, crypto.randomUUID());
      setHistory((current) => [...current, { ...result, movie }]);
      setIndex((current) => current + 1);
      bumpFeed();
      flash(action === "liked" ? "Liked — recommendations updated" : action === "disliked" ? "Disliked — recommendations updated" : action === "watchlist" ? "Added to watchlist" : "Skipped for now");
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
    <Page eyebrow={`Discover your taste · ${activeProfile?.name ?? "movie lover"}`} title="What have you watched?" intro="Right means watched and liked. Left means watched and disliked. Skip and watchlist stay separate. Finish whenever you’re ready.">
      <div className="taste-progress"><span>{history.length} choices saved</span><progress max={Math.max(deck.length, 1)} value={index} aria-label="Onboarding progress" /><button className="button" onClick={undo} disabled={!history.length || !!pending}>Undo latest</button></div>
      {movieState.loading ? <Skeleton count={1} /> : !movie ? <Empty title="You’re caught up" text="There are no more onboarding movies in this selection." /> :
        <section className="swipe-stage" tabIndex={0} aria-label="Movie preference card. Use left and right arrow keys to dislike or like." onKeyDown={(event) => {
          if (event.key === "ArrowRight") void submit("liked");
          if (event.key === "ArrowLeft") void submit("disliked");
          if (event.key.toLowerCase() === "s") void submit("skip");
          if (event.key.toLowerCase() === "w") void submit("watchlist");
          if (event.key.toLowerCase() === "u") void undo();
        }}>
          <article className="swipe-card" style={{ transform: `translateX(${dragX}px) rotate(${dragX / 25}deg)` }} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp}>
            <Poster key={movie.id} movie={movie} large />
            <div className="swipe-copy"><p className="eyebrow">{movie.genres.join(" · ")}</p><h2>{movie.title} {movie.year ? <small>({movie.year})</small> : null}</h2><p>{movie.synopsis}</p>{dragX > 45 && <strong className="swipe-label like">Watched and liked</strong>}{dragX < -45 && <strong className="swipe-label dislike">Watched and disliked</strong>}</div>
          </article>
          <div className="swipe-actions">
            <button className="button dislike-button" disabled={!!pending || readOnly} onClick={() => submit("disliked")}>← Watched and disliked</button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("skip")}>Haven’t watched / Skip <kbd>S</kbd></button>
            <button className="button" disabled={!!pending || readOnly} onClick={() => submit("watchlist")}>＋ Add to watchlist <kbd>W</kbd></button>
            <button className="button like-button" disabled={!!pending || readOnly} onClick={() => submit("liked")}>Watched and liked →</button>
          </div>
        </section>}
      {error && <div className="save-error" role="alert">{error}. Your card was not advanced; retry the action.</div>}
      <div className="sticky-action"><span>No minimum required</span><button className="button primary" onClick={finish} disabled={!activeProfile}>See my recommendations →</button></div>
    </Page>
  );
}

function Home(props: SharedProps) {
  const [discovery, setDiscovery] = useState(35);
  const { data, error, loading } = useRemote(
    () => api.recommendations(props.activeProfile ? [props.activeProfile.id] : [], discovery),
    [props.activeProfile?.id, discovery, props.feedRevision],
  );
  return (
    <Page eyebrow={`Curated for ${props.activeProfile?.name ?? "you"}`} title="Your next favorite is waiting" intro="A considered mix of familiar picks and discoveries, tuned to what you enjoy.">
      <section className="discovery card" aria-labelledby="discovery-title"><div><h2 id="discovery-title">Discovery balance</h2><p>Move toward discovery for less familiar, more adventurous picks.</p></div><div className="range-wrap"><span>Familiar</span><input aria-label="Discovery balance" type="range" min="0" max="100" value={discovery} onChange={(event) => setDiscovery(Number(event.target.value))} /><span>Discovery</span></div></section>
      <SectionTitle title="Top picks for you" action={<Link to="/search">Browse all →</Link>} />
      <AsyncMovies loading={loading} error={error} movies={data} {...props} />
    </Page>
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
  const [refresh, setRefresh] = useState(0);
  const groupState = useRemote(api.groups, [refresh]);
  const [selectedId, setSelectedId] = useState<number>();
  const [name, setName] = useState("");
  const [inviteInfo, setInviteInfo] = useState<{ id: number; url: string }>();
  const [strategy, setStrategy] = useState<"balanced" | "everyone_matters">("balanced");
  const [includeWatched, setIncludeWatched] = useState(false);
  const groups = groupState.data ?? [];
  const selected = groups.find((group) => group.id === selectedId) ?? groups[0];
  const { data, error, loading } = useRemote(
    () => selected && props.activeProfile ? api.groupRecommendations(selected.id, props.activeProfile.id, strategy, includeWatched) : Promise.resolve([]),
    [selected?.id, props.activeProfile?.id, strategy, includeWatched, selected?.recommendationVersion, props.feedRevision],
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
      setInviteInfo({ id: result.id, url: result.invite_url });
      if (navigator.clipboard) {
        await navigator.clipboard.writeText(result.invite_url);
        props.flash("Invitation link copied. It expires in 48 hours.");
      } else {
        props.flash("Invitation created. Copy the link shown below.");
      }
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
  return (
    <Page eyebrow="Movie night" title="Find a film everyone can enjoy" intro="Create a group, invite friends under their own sessions, then rank a shared candidate pool without exposing anyone’s private feedback.">
      {!props.activeProfile ? <Empty title="Create your profile first" text="A profile is needed to own or join a group." action={<Link className="button primary" to="/profiles">Create profile</Link>} /> : <>
        <form className="inline-form group-create card" onSubmit={create}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="New group name" aria-label="New group name" required /><button className="button primary">Create group</button></form>
        {groupState.loading ? <Skeleton count={2} /> : groupState.error ? <ErrorState message={groupState.error} /> : !groups.length ? <Empty title="No groups yet" text="Create one, then generate an expiring invitation link for your friends." /> : <>
          <div className="group-tabs">{groups.map((group) => <button className={`button ${group.id === selected?.id ? "primary" : ""}`} key={group.id} onClick={() => setSelectedId(group.id)}>{group.name}</button>)}</div>
          {selected && <section className="group-summary card"><div><h2>{selected.name}</h2><p>{selected.members.length} member{selected.members.length === 1 ? "" : "s"}</p></div><button className="button" onClick={invite}>Create & copy invitation</button>{inviteInfo && <div className="invite-link"><label><span>Share this expiring link</span><input readOnly value={inviteInfo.url} onFocus={(event) => event.currentTarget.select()} /></label><button className="button" onClick={revokeInvite}>Revoke invitation</button></div>}<div className="member-list">{selected.members.map((member) => <span key={member.profileId}>{member.name} · {member.needsOnboarding ? "needs more onboarding" : `${member.feedbackCount} preferences`}{((selected.ownerProfileId === props.activeProfile?.id && member.profileId !== selected.ownerProfileId) || (member.profileId === props.activeProfile?.id && member.profileId !== selected.ownerProfileId)) && <button onClick={() => removeMember(member.profileId)}>{member.profileId === props.activeProfile?.id ? "Leave" : "Remove"}</button>}</span>)}</div></section>}
          <label className="strategy-row card"><span><strong>Ranking strategy</strong><small>Balanced emphasizes average enjoyment; Everyone matters emphasizes the least-satisfied member.</small></span><select value={strategy} onChange={(event) => setStrategy(event.target.value as typeof strategy)}><option value="balanced">Balanced</option><option value="everyone_matters">Everyone matters</option></select></label>
        <label className="switch-row card"><span><strong>Include already watched movies</strong><small>Off by default so nobody gets a repeat.</small></span><input type="checkbox" role="switch" checked={includeWatched} onChange={(event) => setIncludeWatched(event.target.checked)} /></label>
          {selected && selected.members.length < 2 ? <Empty title="Waiting for friends" text="Share an invitation. Friends join with their own profile and session." /> : <><p className="compromise-note">Group scores are estimates. Conflicts are labeled “Best available compromise”; no result promises everyone will like it.</p><AsyncMovies loading={loading} error={error} movies={data} {...props} /></>}
        </>}
      </>}
    </Page>
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
      const group = await api.joinGroup(token, props.activeProfile.id);
      setState("joined");
      setMessage(`Joined ${group.name}`);
      window.setTimeout(() => navigate("/group"), 800);
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

function EvaluationView() {
  const { data, error, loading } = useRemote(api.evaluation);
  return <Page eyebrow="Model lab" title="Recommendation quality" intro="Offline metrics from the latest leakage-safe evaluation run. Higher values are better.">{loading ? <Skeleton count={3} /> : error ? <ErrorState message={error} /> : !data?.length ? <Empty title="Evaluation pending" text="No benchmark has been generated yet." /> : <div className="metrics-grid">{data.map((row, index) => <article className={`metric-model card ${index === 0 ? "featured" : ""}`} key={row.model}><div className="metric-heading"><h2>{row.model}</h2>{index === 0 && <span className="pill">Active</span>}</div>{(["precisionAt10", "recallAt10", "ndcgAt10", "coverage", "novelty"] as const).map((key) => <div className="metric" key={key}><span>{key.replace("At10", "@10")}</span><strong>{Math.round(row[key] * 100)}%</strong><i><b style={{ width: `${row[key] * 100}%` }} /></i></div>)}</article>)}</div>}</Page>;
}

function SettingsView({ setReadOnly }: { setReadOnly: (value: boolean) => void }) {
  const { data, error, loading } = useRemote(api.settings);
  useEffect(() => { if (data) setReadOnly(data.readOnly); }, [data, setReadOnly]);
  return <Page eyebrow="System" title="Settings & data" intro="See what powers your recommendations and how current the catalog is.">{loading ? <Skeleton count={2} /> : error ? <ErrorState message={error} /> : data && <SettingsCards settings={data} />}</Page>;
}

function SettingsCards({ settings }: { settings: Settings }) {
  return <div className="settings-grid"><section className="card"><p className="eyebrow">Active model</p><h2>{settings.activeModel}</h2><p>The selected model is based on the latest available evaluation, with a safe popularity fallback.</p><Link to="/evaluation">View evaluation →</Link></section><section className="card"><p className="eyebrow">Dataset</p><h2>{settings.dataset}</h2><dl><div><dt>Movies</dt><dd>{settings.movieCount.toLocaleString()}</dd></div><div><dt>Ratings</dt><dd>{settings.ratingCount.toLocaleString()}</dd></div><div><dt>Last trained</dt><dd>{settings.lastTrained ? new Date(settings.lastTrained).toLocaleDateString() : "Pending"}</dd></div><div><dt>Access</dt><dd>{settings.readOnly ? "Read only" : "Read & write"}</dd></div></dl></section><section className="card full"><p className="eyebrow">Metadata</p><h2>Catalog enrichment</h2><p>{settings.tmdbAvailable ? "TMDB enrichment is configured." : "TMDB is not configured; MovieLens metadata and poster placeholders remain available."}</p></section></div>;
}

function AsyncMovies({ loading, error, movies, ...props }: { loading: boolean; error: string; movies?: Movie[] } & SharedProps) {
  if (loading) return <Skeleton count={12} />;
  if (error) return <ErrorState message={error} />;
  if (!movies?.length) return <Empty title="Nothing here yet" text="Adjust your search or filters and try again." />;
  return <div className="movie-grid">{movies.map((movie) => <MovieCard key={movie.id} movie={movie} {...props} />)}</div>;
}

function MovieCard({ movie, ...props }: { movie: Movie } & SharedProps) {
  return <article className="movie-card"><Link to={`/movies/${movie.id}`} aria-label={`View ${movie.title}`}><Poster movie={movie} />{movie.score > 0 && <div className="score">{movie.score}%</div>}</Link><div className="movie-copy"><p className="movie-genres">{movie.genres.join(" · ")}</p><h3><Link to={`/movies/${movie.id}`}>{movie.title}</Link></h3><p>{[movie.year || null, movie.runtime ? `${movie.runtime} min` : null].filter(Boolean).join(" · ")}</p>{movie.reason && <small>{movie.reason}</small>}<MovieActions movie={movie} {...props} compact /></div></article>;
}

function MovieActions({ movie, activeProfile, readOnly, flash, bumpFeed, compact = false }: { movie: Movie; compact?: boolean } & SharedProps) {
  const [pending, setPending] = useState("");
  const act = async (action: MovieAction) => {
    if (!activeProfile || readOnly) return;
    setPending(action);
    try {
      await api.act(activeProfile.id, movie.id, action);
      bumpFeed();
      flash(action === "watchlist" ? "Added to watchlist" : action === "watched" ? "Marked as watched" : "Hidden from recommendations");
    } catch { flash("Couldn’t save that change"); } finally { setPending(""); }
  };
  return <div className={`movie-actions ${compact ? "compact-actions" : ""}`}><button disabled={readOnly || !!pending} onClick={() => act("watchlist")} aria-label={`Add ${movie.title} to watchlist`}>＋ <span>Watchlist</span></button><button disabled={readOnly || !!pending} onClick={() => act("watched")} aria-label={`Mark ${movie.title} watched`}>✓ <span>Watched</span></button><button disabled={readOnly || !!pending} onClick={() => act("dismiss")} aria-label={`Dismiss ${movie.title}`}>× <span>Not for me</span></button></div>;
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

function Page({ eyebrow, title, intro, children }: { eyebrow?: string; title: string; intro?: string; children: ReactNode }) {
  return <div className="page"><header className="page-title">{eyebrow && <p className="eyebrow">{eyebrow}</p>}{title && <h1>{title}</h1>}{intro && <p>{intro}</p>}</header>{children}</div>;
}
function SectionTitle({ title, action }: { title: string; action?: ReactNode }) { return <div className="section-title"><h2>{title}</h2>{action}</div>; }
function Filter({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) { return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{values.map((item) => <option key={item}>{item}</option>)}</select></label>; }
function Skeleton({ count }: { count: number }) { return <div className="movie-grid" aria-label="Loading"><span className="sr-only">Loading results</span>{Array.from({ length: count }, (_, index) => <div className="skeleton" key={index}><i /><b /><b /></div>)}</div>; }
function ErrorState({ message }: { message: string }) { return <div className="state error" role="alert"><span>!</span><h2>Something went wrong</h2><p>{message || "Please try again shortly."}</p><button className="button" onClick={() => location.reload()}>Try again</button></div>; }
function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) { return <div className="state"><span>◇</span><h2>{title}</h2><p>{text}</p>{action}</div>; }
function NotFound() { return <Page title="That page wandered off"><Empty title="404" text="Let’s get you back to something worth watching." action={<Link className="button primary" to="/">Go home</Link>} /></Page>; }
function Footer() { return <footer><span>CineMatch</span><p>Recommendations with a reason. This product uses the TMDB API but is not endorsed or certified by TMDB.</p><Link to="/profiles">Profiles</Link><Link to="/settings">Settings</Link></footer>; }

export default App;
