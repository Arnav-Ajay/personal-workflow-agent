"""Read-only Numerai Classic queries with an explicit performance-field allowlist."""

RETURN_FIELDS = """
  allTime allTimeNmr oneDay oneDayNmr oneYear oneYearNmr
  threeMonths threeMonthsNmr
"""
MODEL_RETURN_FIELDS = RETURN_FIELDS + """
  theoreticalAllTime theoreticalOneDay theoreticalOneYear theoreticalThreeMonths
"""
ACCOUNT_SCORE_FIELDS = """
  date seasonRank alpha alphaLtm alphaLtmRank corr corrLtm corrLtmRank
  corr60 corr60Ltm corr60LtmRank mmc mmcLtm mmcLtmRank mmc60 mmc60Ltm mmc60LtmRank
  tc tcLtm tcLtmRank fncV4 fncV4Ltm fncV4LtmRank v2Corr20 v2Corr20Ltm v2Corr20LtmRank
  mpc mpcLtm mpcLtmRank neutralCorr neutralCorrLtm neutralCorrLtmRank
  neutralMmc neutralMmcLtm neutralMmcLtmRank neutralBmc neutralBmcLtm neutralBmcLtmRank
"""
DAILY_MODEL_FIELDS = """
  date alphaRep alphaRank corrRep corrRank corr60Rep corr60Rank corrV4Rep corrV4Rank
  mmcRep mmcRank fncRep fncRank fncV3Rep fncV3Rank fncV4Rep fncV4Rank
  tcRep tcRank icRep icRank icV2Rep icV2Rank mpcRep mpcRank
  neutralCorrRep neutralCorrRank neutralMmcRep neutralMmcRank neutralBmcRep neutralBmcRank
  apy return13Weeks return52Weeks
"""
SCORE_FIELDS = """
  roundId submissionId roundNumber roundResolveTime roundScoreTime roundCloseStakingTime
  displayName version value percentile date day resolveDate resolved payoutPending payoutSettled
"""

ACCOUNT = """
query Account {
  account {
    username availableNmr availableStakeCredit heldForFusionStakes
    heldForPendingWithdrawals heldForScheduledStakeIncreases onChainWalletBalance
    models { id name tournament archived }
  }
}
"""

OVERVIEW = """
query Overview($username: String!, $model: String!, $tournament: Int!) {
  accountProfile(username: $username, tournament: $tournament) {
    username tournament totalStake
    returns { RETURN_FIELDS }
    scores { ACCOUNT_SCORE_FIELDS }
    returnsTs { date allTime allTimeNmr oneYear oneYearNmr threeMonths threeMonthsNmr }
    scoresTs { ACCOUNT_SCORE_FIELDS }
    totalStakeTs { date time value delta }
  }
  v3UserProfile(modelName: $model, tournament: $tournament) {
    id username accountName tournament startDate isActive stakeValue
    latestReturns { MODEL_RETURN_FIELDS }
    latestUserScores { date displayName rank reputation stakedRank }
    returns { date MODEL_RETURN_FIELDS }
    stakeValues { time value delta }
    dailyModelPerformances { DAILY_MODEL_FIELDS }
  }
}
""".replace("MODEL_RETURN_FIELDS", MODEL_RETURN_FIELDS).replace("RETURN_FIELDS", RETURN_FIELDS).replace(
    "ACCOUNT_SCORE_FIELDS", ACCOUNT_SCORE_FIELDS
).replace("DAILY_MODEL_FIELDS", DAILY_MODEL_FIELDS)

ROUNDS = """
query ModelRounds($modelId: ID!, $tournament: Int!, $limit: Int!, $offset: Int!) {
  v2RoundModelPerformances(modelId: $modelId, tournament: $tournament,
                          limit: $limit, offset: $offset, submittedOnly: true,
                          distinctOnRound: true) {
    roundId roundNumber roundTarget roundOpenTime roundScoreTime roundCloseStakingTime
    roundResolveTime roundResolved roundDataDatestamp submissionId
    atRisk payout roundPayoutFactor
    payoutMultipliers { id scoreConfigId name version displayName multiplier }
    submissionScores { SCORE_FIELDS }
  }
}
""".replace("SCORE_FIELDS", SCORE_FIELDS)

# The current service supports pagination, but not the SDK's newer roundNumberEq
# argument. Fetch detailed histories by their verified positions in that listing.
ROUND_HISTORY = ROUNDS.replace("query ModelRounds", "query RoundHistory").replace(
    "submissionScores {", "intraRoundSubmissionScores {"
)
