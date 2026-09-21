import { API } from 'api';
import { createApi, fetchBaseQuery } from '@reduxjs/toolkit/query/react';

import fetchBaseQueryHeaders from 'libs/fetchBaseQueryHeaders';

export const fleetApi = createApi({
    reducerPath: 'fleetApi',
    baseQuery: fetchBaseQuery({
        prepareHeaders: fetchBaseQueryHeaders,
    }),

    tagTypes: ['Fleet', 'Fleets', 'VastPreferredMachines'],

    endpoints: (builder) => ({
        getFleets: builder.query<IFleet[], TFleetListRequestParams>({
            query: (body) => {
                return {
                    url: API.FLEETS.LIST(),
                    method: 'POST',
                    body,
                };
            },

            providesTags: (result) =>
                result ? [...result.map(({ name }) => ({ type: 'Fleet' as const, id: name })), 'Fleets'] : ['Fleets'],
        }),

        getProjectFleets: builder.query<IFleet[], { projectName: IProject['project_name']; includeImported?: boolean }>({
            query: ({ projectName, includeImported }) => {
                return {
                    url: API.PROJECTS.FLEETS(projectName),
                    method: 'POST',
                    ...(typeof includeImported === 'boolean'
                        ? {
                              body: {
                                  include_imported: includeImported,
                              },
                          }
                        : {}),
                };
            },

            providesTags: (result) =>
                result ? [...result.map(({ name }) => ({ type: 'Fleet' as const, id: name })), 'Fleets'] : ['Fleets'],
        }),

        getFleetDetails: builder.query<
            IFleet,
            { projectName: IProject['project_name']; fleetName?: IFleet['name']; fleetId?: IFleet['id'] }
        >({
            query: ({ projectName, fleetName, fleetId }) => {
                return {
                    url: API.PROJECTS.FLEETS_DETAILS(projectName),
                    method: 'POST',
                    body: {
                        name: fleetName,
                        id: fleetId,
                    },
                };
            },

            providesTags: (result) => (result ? [{ type: 'Fleet' as const, id: result.name }] : []),
        }),

        deleteFleet: builder.mutation<IFleet[], { projectName: IProject['project_name']; fleetNames: string[] }>({
            query: ({ projectName, fleetNames }) => {
                return {
                    url: API.PROJECTS.FLEETS_DELETE(projectName),
                    method: 'POST',
                    body: { names: fleetNames },
                };
            },

            invalidatesTags: ['Fleets'],
        }),

        applyFleet: builder.mutation<IFleet, IApplyFleetPlanRequestRequest & { projectName: IProject['project_name'] }>({
            query: ({ projectName, ...body }) => {
                return {
                    url: API.PROJECTS.FLEETS_APPLY(projectName),
                    method: 'POST',
                    body,
                };
            },

            invalidatesTags: ['Fleets'],
        }),

        importVastInstance: builder.mutation<IFleet, IImportVastInstanceRequest & { projectName: IProject['project_name'] }>({
            query: ({ projectName, ...body }) => ({
                url: API.PROJECTS.FLEETS_IMPORT_VAST_INSTANCE(projectName),
                method: 'POST',
                body,
            }),
            invalidatesTags: ['Fleets', 'VastPreferredMachines'],
        }),

        getVastPreferredMachines: builder.query<IVastPreferredMachinesResponse, { projectName: IProject['project_name'] }>({
            query: ({ projectName }) => ({
                url: API.PROJECTS.FLEETS_VAST_PREFERRED_MACHINES_LIST(projectName),
                method: 'POST',
            }),
            providesTags: ['VastPreferredMachines'],
        }),

        addVastPreferredMachine: builder.mutation<
            IVastPreferredMachinesResponse,
            IVastPreferredMachineRequest & { projectName: IProject['project_name'] }
        >({
            query: ({ projectName, ...body }) => ({
                url: API.PROJECTS.FLEETS_VAST_PREFERRED_MACHINES_ADD(projectName),
                method: 'POST',
                body,
            }),
            invalidatesTags: ['VastPreferredMachines'],
        }),

        deleteVastPreferredMachine: builder.mutation<
            IVastPreferredMachinesResponse,
            IVastPreferredMachineRequest & { projectName: IProject['project_name'] }
        >({
            query: ({ projectName, ...body }) => ({
                url: API.PROJECTS.FLEETS_VAST_PREFERRED_MACHINES_DELETE(projectName),
                method: 'POST',
                body,
            }),
            invalidatesTags: ['VastPreferredMachines'],
        }),
    }),
});

export const {
    useGetFleetsQuery,
    useLazyGetFleetsQuery,
    useGetProjectFleetsQuery,
    useLazyGetProjectFleetsQuery,
    useDeleteFleetMutation,
    useGetFleetDetailsQuery,
    useApplyFleetMutation,
    useImportVastInstanceMutation,
    useGetVastPreferredMachinesQuery,
    useAddVastPreferredMachineMutation,
    useDeleteVastPreferredMachineMutation,
} = fleetApi;
