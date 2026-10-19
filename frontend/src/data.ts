import type { Evaluation, Movie, Settings } from "./types";

export const movies: Movie[] = [
  { id: 1, title: "Arrival", year: 2016, genres: ["Sci-Fi", "Drama"], language: "English", runtime: 116, score: 96, reason: "Because you loved thoughtful science fiction", synopsis: "A linguist works with the military to communicate with mysterious visitors, changing how she understands time." },
  { id: 2, title: "Parasite", year: 2019, genres: ["Thriller", "Drama"], language: "Korean", runtime: 132, score: 94, reason: "Critically acclaimed social thrillers", synopsis: "A struggling family gradually infiltrates the home of a wealthy household, with unexpected consequences." },
  { id: 3, title: "The Grand Budapest Hotel", year: 2014, genres: ["Comedy", "Adventure"], language: "English", runtime: 100, score: 91, reason: "Matches your taste for witty, stylized stories", synopsis: "A legendary concierge and his lobby boy become caught in a caper involving a priceless painting." },
  { id: 4, title: "Portrait of a Lady on Fire", year: 2019, genres: ["Romance", "Drama"], language: "French", runtime: 122, score: 89, reason: "A quiet, character-driven discovery", synopsis: "On an isolated island, an artist is commissioned to paint a young woman's wedding portrait in secret." },
  { id: 5, title: "Spider-Man: Into the Spider-Verse", year: 2018, genres: ["Animation", "Action"], language: "English", runtime: 117, score: 93, reason: "High-energy stories with inventive worlds", synopsis: "Teen Miles Morales becomes Spider-Man and joins other Spider-people from parallel universes." },
  { id: 6, title: "Moonlight", year: 2016, genres: ["Drama"], language: "English", runtime: 111, score: 90, reason: "Award-winning intimate dramas", synopsis: "A young man navigates identity, family, and belonging across three defining chapters of his life." },
  { id: 7, title: "Spirited Away", year: 2001, genres: ["Animation", "Fantasy"], language: "Japanese", runtime: 125, score: 95, reason: "Imaginative worlds with emotional depth", synopsis: "A young girl enters a world ruled by spirits and must find courage to save her parents." },
  { id: 8, title: "Knives Out", year: 2019, genres: ["Mystery", "Comedy"], language: "English", runtime: 130, score: 88, reason: "Clever ensemble mysteries", synopsis: "A detective investigates the death of a novelist amid his eccentric and combative family." },
  { id: 9, title: "Past Lives", year: 2023, genres: ["Romance", "Drama"], language: "English", runtime: 106, score: 92, reason: "Reflective modern romances", synopsis: "Childhood friends reunite in New York decades after one family emigrated from South Korea." },
  { id: 10, title: "Mad Max: Fury Road", year: 2015, genres: ["Action", "Sci-Fi"], language: "English", runtime: 120, score: 87, reason: "Visually bold, propulsive adventures", synopsis: "Rebels flee a tyrant across a ruined wasteland in a roaring war rig." },
];

export const evaluation: Evaluation[] = [];

export const settings: Settings = {
  activeModel: "Demo fallback (not benchmarked)",
  dataset: "Built-in UI demo catalogue",
  movieCount: movies.length,
  ratingCount: 0,
  readOnly: false,
  tmdbAvailable: false,
};
