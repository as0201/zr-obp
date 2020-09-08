import argparse
from pathlib import Path
import yaml

import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.experimental import enable_hist_gradient_boosting
from sklearn.ensemble import HistGradientBoostingClassifier

from custom_dataset import OBDWithInteractionFeatures
from obp.policy import IPWLearner
from obp.ope import InverseProbabilityWeighting

# hyperparameter for the regression model used in model dependent OPE estimators
with open("./conf/hyperparams.yaml", "rb") as f:
    hyperparams = yaml.safe_load(f)

base_model_dict = dict(
    logistic_regression=LogisticRegression,
    lightgbm=HistGradientBoostingClassifier,
    random_forest=RandomForestClassifier,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="run counterfactual policy selection.")
    parser.add_argument(
        "--context_set",
        type=str,
        choices=["1", "2"],
        required=True,
        help="context sets for contextual bandit policies.",
    )
    parser.add_argument(
        "--base_model",
        type=str,
        choices=["logistic_regression", "lightgbm", "random_forest",],
        required=True,
        help="base model for a counterfactual policy to be evaluated",
    )
    parser.add_argument(
        "--behavior_policy",
        type=str,
        choices=["bts", "random"],
        default="random",
        help="behavior policy, bts or random.",
    )
    parser.add_argument(
        "--campaign",
        type=str,
        choices=["all", "men", "women"],
        required=True,
        help="campaign name, men, women, or all.",
    )
    parser.add_argument(
        "--test_size",
        type=float,
        default=0.3,
        help="the proportion of the dataset to include in the test split.",
    )
    parser.add_argument("--random_state", type=int, default=12345)
    args = parser.parse_args()
    print(args)

    context_set = args.context_set
    base_model = args.base_model
    behavior_policy = args.behavior_policy
    campaign = args.campaign
    test_size = args.test_size
    random_state = args.random_state
    np.random.seed(random_state)
    data_path = Path("../open_bandit_dataset")

    # define a dataset class
    obd = OBDWithInteractionFeatures(
        behavior_policy=behavior_policy,
        campaign=campaign,
        data_path=data_path,
        context_set=context_set,
    )
    # define a counterfactual policy
    counterfactual_policy = IPWLearner(
        base_model=base_model_dict[base_model](**hyperparams[base_model]),
        n_actions=obd.n_actions,
        len_list=obd.len_list,
    )
    policy_name = f"{base_model}_{context_set}"

    # obtain batch logged bandit feedback generated by behavior policy
    bandit_feedback = obd.obtain_batch_bandit_feedback(
        test_size=test_size, is_timeseries_split=True,
    )
    # ground-truth policy value of the Bernoulli TS policy (the current best policy) in the test set
    # , which is the empirical mean of the factual (observed) rewards (on-policy estimation)
    ground_truth = obd.calc_on_policy_policy_value_estimate(
        behavior_policy="bts",
        campaign=campaign,
        data_path=data_path,
        test_size=test_size,
        is_timeseries_split=True,
    )

    # train an evaluation on the training set of the logged bandit feedback data
    action_dist = counterfactual_policy.fit(
        context=bandit_feedback["context"],
        action=bandit_feedback["action"],
        reward=bandit_feedback["reward"],
        pscore=bandit_feedback["pscore"],
        position=bandit_feedback["position"],
    )
    # make predictions
    action_dist = counterfactual_policy.predict(context=bandit_feedback["context_test"])
    # estimate the policy value of a given counterfactual algorithm by the three OPE estimators.
    ipw = InverseProbabilityWeighting()
    estimated_policy_value = ipw.estimate_policy_value(
        reward=bandit_feedback["reward_test"],
        action=bandit_feedback["action_test"],
        position=bandit_feedback["position_test"],
        pscore=bandit_feedback["pscore_test"],
        action_dist=action_dist,
    )
    estimated_interval = ipw.estimate_interval(
        reward=bandit_feedback["reward_test"] / ground_truth,
        action=bandit_feedback["action_test"],
        position=bandit_feedback["position_test"],
        pscore=bandit_feedback["pscore_test"],
        action_dist=action_dist,
        random_state=random_state,
    )
    ope_results_df = pd.DataFrame(estimated_interval, index=["ipw"])
    ope_results_df["relative_estimated_policy_value"] = (
        estimated_policy_value / ground_truth
    )

    # calculate estimated policy value relative to that of the behavior policy
    print("=" * 70)
    print(f"random_state={random_state}: counterfactual policy={policy_name}")
    print("-" * 70)
    print(ope_results_df)
    print("=" * 70)

    # save counterfactual policy evaluation results in `./logs` directory
    save_path = Path("./logs") / behavior_policy / campaign / "cf_policy_selection"
    save_path.mkdir(exist_ok=True, parents=True)
    ope_results_df.to_csv(save_path / f"{policy_name}.csv")
