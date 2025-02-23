# -*- coding: utf-8 -*-

"""
This module contains the implementation of USTGNN model.

Classes:
    - py:class:`SPTempGNN`: Spatio-temporal graph neural network.
    - py:class:`CombinedGNN`: Combined graph neural network.
"""

__author__ = "Mir Sazzat Hossain"

import torch
import torch.nn as nn
import torch.nn.functional as f


class SPTempGNN(nn.Module):
    """Spatio-temporal graph neural network."""

    def __init__(
        self,
        d_temporal: torch.Tensor,
        a_temporal: torch.Tensor,
        num_timestamps: int,
        out_size: int,
        total_nodes: int,
        device: torch.device,
    ) -> None:
        """
        Initialize the SPTempGNN class.

        :param d_temporal: temporal distance matrix
        :type d_temporal: torch.Tensor
        :param a_temporal: temporal adjacency matrix
        :type a_temporal: torch.Tensor
        :param num_timestamps: number of timestamps
        :type num_timestamps: int
        :param out_size: output size
        :type out_size: int
        :param total_nodes: total number of nodes
        :type total_nodes: int
        :param device: device to use
        :type device: str
        """
        super(SPTempGNN, self).__init__()
        self.total_nodes = total_nodes
        self.spatial_temp = torch.mm(
            d_temporal, torch.mm(a_temporal, d_temporal))
        self.his_temporal_weight = nn.Parameter(
            nn.init.xavier_uniform_(torch.empty(num_timestamps, out_size))
        ).to(device)
        self.his_final_weight = nn.Parameter(
            nn.init.xavier_uniform_(torch.empty(2*out_size, out_size))
        ).to(device)

    def forward(self, his_raw_features: torch.Tensor) -> torch.Tensor:
        """
        Forward pass of the SPTempGNN class.

        :param his_raw_features: raw features of the history
        :type his_raw_features: torch.Tensor

        :return: output of the SPTempGNN
        :rtype: torch.Tensor
        """
        his_self = his_raw_features
        his_temporal = self.his_temporal_weight.repeat(
            self.total_nodes, 1) * his_raw_features
        his_temporal = torch.mm(self.spatial_temp, his_temporal)
        his_combined = torch.cat([his_self, his_temporal], dim=1)
        his_final = torch.mm(his_combined, self.his_final_weight)
        his_raw_features = f.relu(his_final)

        return his_raw_features


class CombinedGNN(nn.Module):
    """Combined spatio-temporal graph neural network."""

    def __init__(
        self,
        out_size: int,
        adj_matrix: torch.Tensor,
        device: torch.device,
        start_time: int,
        num_gnn_layers: int,
        num_timestamps: int,
        num_days: int,
    ) -> None:
        """
        Initialize the CombinedGNN class.

        :param out_size: output size
        :type out_size: int
        :param adj_matrix: adjacency matrix
        :type adj_matrix: torch.Tensor
        :param device: device to use
        :type device: str
        :param start_time: start time
        :type start_time: int
        :param num_gnn_layers: number of GNN layers
        :type num_gnn_layers: int
        :param num_timestamps: number of timestamps
        :type num_timestamps: int
        :param num_days: number of days
        :type num_days: int
        """
        super(CombinedGNN, self).__init__()
        self.out_size = out_size
        self.adj_matrix = adj_matrix
        self.device = device
        self.start_time = start_time
        self.num_gnn_layers = num_gnn_layers
        self.num_timestamps = num_timestamps
        self.num_days = num_days

        # total nodes (95 in our case)
        self.total_nodes = self.adj_matrix.shape[0]

        self.his_weight = nn.Parameter(
            nn.init.xavier_uniform_(torch.empty(
                self.out_size, self.num_timestamps*out_size)))
        self.cur_weight = nn.Parameter(
            nn.init.xavier_uniform_(torch.empty(1, self.num_timestamps*1)))

        a = self.adj_matrix
        dim = self.num_timestamps * self.total_nodes

        a_temporal = torch.zeros(dim, dim).to(self.device)
        d_temporal = torch.zeros(dim, dim).to(self.device)
        identity = torch.eye(self.total_nodes).to(self.device)

        for i in range(self.num_timestamps):
            for j in range(0, i+1):
                row_start = i * self.total_nodes
                row_end = row_start + self.total_nodes
                col_start = j * self.total_nodes
                col_end = col_start + self.total_nodes

                if i == j:
                    a_temporal[row_start:row_end, col_start:col_end] = a
                else:
                    a_temporal[row_start:row_end,
                               col_start:col_end] = identity + a

        row_sum = torch.sum(a_temporal, dim=0)

        for i in range(dim):
            d_temporal[i, i] = 1/max(torch.sqrt(row_sum[i]), torch.tensor(1))

        for i in range(self.num_gnn_layers):
            sp_temp_gnn = SPTempGNN(
                d_temporal,
                a_temporal,
                self.num_timestamps,
                self.out_size,
                self.total_nodes,
                self.device,
            )
            setattr(self, f'sp_temp_gnn_{i}', sp_temp_gnn)

        dim_2 = self.num_timestamps * self.out_size
        self.final_weight = nn.Parameter(
            nn.init.xavier_uniform_(torch.empty(dim_2, dim_2)))

        self.init_params()

    def init_params(self) -> None:
        """Initialize parameters."""
        for param in self.parameters():
            if len(param.shape) > 1:
                nn.init.xavier_uniform_(param)

    def forward(
        self,
        his_raw_features: torch.Tensor
    ) -> torch.tensor:
        """
        Forward pass of the CombinedGNN class.

        :param his_raw_features: raw features of the history
        :type his_raw_features: torch.Tensor

        :return: output of the CombinedGNN
        :rtype: torch.Tensor
        """
        dim = self.num_timestamps * self.total_nodes
        his_raw_features = his_raw_features[:, :, :self.num_days].view(
            dim, self.num_days)

        for i in range(self.num_gnn_layers):
            sp_temp_gnn = getattr(self, f'sp_temp_gnn_{i}')
            his_raw_features = sp_temp_gnn(his_raw_features)
            # check if the output is nan
            if torch.isnan(his_raw_features).any():
                print('nan', i)
                exit()

        his_list = []

        for i in range(self.num_timestamps):
            start = i * self.total_nodes
            end = (i+1) * self.total_nodes
            his_list.append(his_raw_features[start:end, :])

        his_final_embds = torch.cat(his_list, dim=1)
        final_embds = f.relu(self.final_weight.mm(his_final_embds.t()).t())
        return final_embds
