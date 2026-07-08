
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Team(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True)
    league = Column(String) # e.g., 'NFL', 'NBA', 'MLB', 'MLS'
    name = Column(String)
    
    # Relationships
    players = relationship("Player", back_populates="team")

class Player(Base):
    __tablename__ = 'players'
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey('teams.id'))
    name = Column(String)
    position = Column(String)
    
    # Relationships
    team = relationship("Team", back_populates="players")
    stats = relationship("PlayerStat", back_populates="player")

class Game(Base):
    __tablename__ = 'games'
    id = Column(Integer, primary_key=True)
    league = Column(String)
    game_date = Column(DateTime)
    home_team_id = Column(Integer, ForeignKey('teams.id'))
    away_team_id = Column(Integer, ForeignKey('teams.id'))

class PlayerStat(Base):
    __tablename__ = 'player_stats'
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey('players.id'))
    game_id = Column(Integer, ForeignKey('games.id'))
    
    # JSON column handles variable stats (e.g., {"points": 25, "assists": 8} for NBA 
    # or {"strikeouts": 7, "walks": 2} for MLB)
    metrics = Column(JSON) 

    # Relationships
    player = relationship("Player", back_populates="stats")

class BettingOdds(Base):
    __tablename__ = 'betting_odds'
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey('games.id'))
    
    # If this is a team bet, player_id is None. If it's a player prop, it links to the player.
    player_id = Column(Integer, ForeignKey('players.id'), nullable=True) 
    
    bet_type = Column(String) # e.g., 'Moneyline', 'Over/Under', 'Player Points'
    line = Column(Float) # e.g., 22.5 (for an Over/Under points prop)
    odds = Column(Integer) # e.g., -110 or +150
    implied_probability = Column(Float) # The math we discussed earlier